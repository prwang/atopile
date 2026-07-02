# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
from itertools import chain

from atopile.config import config as gcfg
from atopile.layout import SubAddress
from faebryk.exporters.pcb.kicad.transformer import (
    PCB_Transformer,
    get_all_geos,
)
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.util import (
    KeyErrorNotFound,
    find,
    find_or,
    groupby,
    not_none,
    once,
    try_or,
)

logger = logging.getLogger(__name__)

PCB = kicad.pcb.KicadPcb
type Footprint = kicad.pcb.Footprint


class LayoutSync:
    """Handles layout synchronization between PCB files."""

    def __init__(self, pcb: PCB):
        self.pcb = pcb

        fps = self.pcb.footprints
        sub_fps = [
            (fp, sub_addr) for fp in fps if (sub_addr := self._get_sub_address(fp))
        ]

        # rooms: reuse-instance footprints grouped by room name (= sheetname,
        # BACKLOG §C3). The carrier is the footprint sheetname; the room name is
        # derived purely from the ato address (no KiCad group is ever involved).
        self.rooms = groupby(sub_fps, lambda x: self._get_room_name(x[1], x[0]))
        for room_name, fps in self.rooms.items():
            pcb_names = {x[1].pcb_address for x in fps}
            assert len(pcb_names) == 1, (
                f"Multiple PCB names found for room {room_name}: {pcb_names}"
            )

    def _get_all_sub_addresses(self, fp: Footprint) -> list[SubAddress]:
        sub_addresses = Property.try_get_property(fp.propertys, "atopile_subaddresses")
        if not sub_addresses:
            return []
        return [
            SubAddress.deserialize(addr)
            for addr in sub_addresses.removeprefix("[").removesuffix("]").split(", ")
        ]

    def _get_sub_address(self, fp: Footprint) -> SubAddress | None:
        return self._choose_sublayout(self._get_all_sub_addresses(fp))

    @once
    def _get_pcb(self, pcb_address: str) -> PCB:
        path = gcfg.project.paths.root / pcb_address
        return kicad.loads(kicad.pcb.PcbFile, path).kicad_pcb

    def _get_room_name(self, sub_addr: SubAddress, fp: Footprint) -> str:
        base_addr = self._get_footprint_addr(fp)
        assert base_addr
        inner = sub_addr.module_address
        return base_addr.removesuffix("." + inner)

    def _choose_sublayout(self, sub_addr: list[SubAddress]) -> SubAddress | None:
        addr_to_pcb = {
            x: pcb
            for x in sub_addr
            if (pcb := try_or(lambda: self._get_pcb(x.pcb_address), None))
        }
        if not addr_to_pcb:
            return None

        # prefer sublayouts with tracks if any exist
        candidates = addr_to_pcb
        if any(pcb.segments for pcb in candidates.values()):
            candidates = {
                sub_addr: pcb for sub_addr, pcb in addr_to_pcb.items() if pcb.segments
            }

        # Heuristic: prefer higher level modules
        candidate = max(
            candidates.items(),
            key=lambda x: len(x[0].module_address.split(".")),
        )
        return candidate[0]

    def _get_footprint_addr(self, fp: Footprint) -> str | None:
        """Get the address of a footprint."""
        return Property.try_get_property(fp.propertys, "atopile_address")

    def sync_rooms(self):
        """Write each reuse-instance footprint's room identity onto its
        `sheetname` (KiCad's source-correspondence channel, BACKLOG §C3).

        atopile creates ZERO KiCad groups and touches no existing group, so a
        user's manual group survives by construction (the A4 failure mode is
        gone). Stale routes of a removed instance are not cleaned here: their net
        vanishes with the instance and KiCad garbage-collects net-0 copper on
        save (fact 9)."""
        for room_name, fps in self.rooms.items():
            logger.debug(f"Tagging room {room_name}")
            for fp, _sub_addr in fps:
                fp.sheetname = room_name
                fp.sheetfile = f"{room_name}.kicad_sch"

    def _generate_net_map(
        self, source_pcb: PCB, target_pcb: PCB, addr_map: dict[str, str]
    ) -> dict[str, str]:
        """Generate mapping from source net names to target net names."""
        net_map: dict[str, str] = {}
        mapping_counts: dict[str, dict[str, int]] = {}

        # Get footprints by address for both boards
        source_fps: dict[str, kicad.pcb.Footprint] = {}
        for fp in source_pcb.footprints:
            addr = self._get_footprint_addr(fp)
            if addr:
                source_fps[addr] = fp

        target_fps: dict[str, kicad.pcb.Footprint] = {}
        for fp in target_pcb.footprints:
            addr = self._get_footprint_addr(fp)
            if addr:
                target_fps[addr] = fp

        # Map nets based on pad connections
        for src_addr, tgt_addr in addr_map.items():
            if src_addr not in source_fps or tgt_addr not in target_fps:
                continue

            src_fp = source_fps[src_addr]
            tgt_fp = target_fps[tgt_addr]

            # Match pads by number
            for src_pad in src_fp.pads:
                pad_name = src_pad.name
                tgt_pads = [p for p in tgt_fp.pads if p.name == pad_name]

                if len(tgt_pads) == 1:
                    tgt_pad = tgt_pads[0]
                elif len(tgt_pads) > 1:
                    # Match by size if multiple pads with same number
                    best_match = min(
                        tgt_pads,
                        key=lambda p: abs(p.size.w - src_pad.size.w)
                        + (
                            abs(p.size.h - src_pad.size.h)
                            if p.size.h and src_pad.size.h
                            else 0
                        ),
                    )
                    tgt_pad = best_match
                else:
                    continue

                # Map the nets
                if (
                    src_pad.net
                    and tgt_pad.net
                    and src_pad.net.name
                    and tgt_pad.net.name
                ):
                    src_net = src_pad.net.name
                    tgt_net = tgt_pad.net.name

                    if src_net not in mapping_counts:
                        mapping_counts[src_net] = {}
                    mapping_counts[src_net][tgt_net] = (
                        mapping_counts[src_net].get(tgt_net, 0) + 1
                    )

                    # Use most frequent mapping
                    if src_net not in net_map or mapping_counts[src_net][tgt_net] > max(
                        mapping_counts[src_net].values()
                    ):
                        net_map[src_net] = tgt_net

        return net_map

    def _sync_footprints(
        self,
        sub_pcb: PCB,
        top_pcb: PCB,
        addr_map: dict[str, str],
        net_map: dict[str, str],
        offset: kicad.pcb.Xy,
    ):
        """Sync footprint positions from source to target."""
        # Get footprints by address
        sub_fps: dict[str, kicad.pcb.Footprint] = {
            addr: fp
            for fp in sub_pcb.footprints
            if (addr := self._get_footprint_addr(fp))
        }

        top_fps: dict[str, kicad.pcb.Footprint] = {
            addr: fp
            for fp in top_pcb.footprints
            if (addr := self._get_footprint_addr(fp))
        }

        # Sync positions
        for src_addr, tgt_addr in addr_map.items():
            if src_addr not in sub_fps or tgt_addr not in top_fps:
                continue

            sub_fp = sub_fps[src_addr]
            top_fp = top_fps[tgt_addr]

            PCB_Transformer.move_fp(
                top_fp, kicad.geo.add(sub_fp.at, offset), sub_fp.layer
            )

        # Non-atopile footprints
        new_objects = []
        manual_fps = [
            fp for fp in sub_pcb.footprints if not self._get_footprint_addr(fp)
        ]
        for sub_fp in manual_fps:
            top_fp = kicad.copy(sub_fp)
            top_fp.uuid = kicad.gen_uuid()

            for pad in top_fp.pads:
                pad.uuid = kicad.gen_uuid()
                if pad.net and pad.net.name in net_map:
                    top_net_name = net_map[pad.net.name]
                    pad.net = kicad.pcb.Net(
                        number=self._get_net_number(top_pcb, top_net_name),
                        name=top_net_name,
                    )
                else:
                    pad.net = None

            new_objects.append(top_fp)

            PCB_Transformer.move_fp(
                top_fp, kicad.geo.add(sub_fp.at, offset), sub_fp.layer
            )

        return new_objects

    def _sync_routes(
        self,
        sub_pcb: PCB,
        top_pcb: PCB,
        net_map: dict[str, str],
        offset: kicad.pcb.Xy,
    ):
        new_objects = []
        for track in chain(sub_pcb.segments, sub_pcb.arcs, sub_pcb.zones, sub_pcb.vias):
            # Get source net name
            sub_net: kicad.pcb.Net | None = find_or(
                sub_pcb.nets,
                lambda n: n.number == track.net,
                None,  # type: ignore
            )

            # Create new track
            new_track: (
                kicad.pcb.Segment
                | kicad.pcb.ArcSegment
                | kicad.pcb.Zone
                | kicad.pcb.Via
            ) = kicad.copy(track)
            new_track.uuid = kicad.gen_uuid()
            if sub_net and sub_net.name in net_map:
                new_track.net = self._get_net_number(top_pcb, net_map[sub_net.name])
                if isinstance(new_track, kicad.pcb.Zone):
                    new_track.net_name = net_map[sub_net.name]
            else:
                new_track.net = 0

            PCB_Transformer.move_object(new_track, offset)
            new_objects.append(new_track)

        return new_objects

    def _sync_other(self, sub_pcb: PCB, top_pcb: PCB, offset: kicad.pcb.Xy):
        new_graphics = []
        for gr in chain(
            get_all_geos(sub_pcb),
            sub_pcb.gr_text_boxes,
            sub_pcb.gr_texts,
            sub_pcb.images,
            # TODO tables are weird about uuids
            # + sub_pcb.tables
        ):
            new_gr = kicad.copy(gr)
            new_gr.uuid = kicad.gen_uuid()

            PCB_Transformer.move_object(new_gr, offset)
            new_graphics.append(new_gr)

        return new_graphics

    def _calculate_room_offset(
        self,
        source_pcb: PCB,
        room_name: str,
    ) -> kicad.pcb.Xy:
        """Calculate offset to apply when pulling a room layout.

        The anchor is the room's largest-by-pad-count footprint, taken from the
        room membership (self.rooms) — NOT from a KiCad group's member list,
        which no longer exists (BACKLOG §C3)."""

        ZERO = kicad.pcb.Xy(x=0, y=0)
        room_fps = [fp for fp, _ in self.rooms.get(room_name, [])]

        if not room_fps:
            return ZERO

        # Find anchor by pad count
        anchor_fp = max(
            room_fps,
            key=lambda fp: len(fp.pads),
        )
        top_pos = anchor_fp.at

        # Find corresponding source footprint
        target_addr = not_none(self._get_sub_address(anchor_fp)).module_address

        # Find in source
        try:
            sub_fp = find(
                source_pcb.footprints,
                lambda fp: self._get_footprint_addr(fp) == target_addr,
            )
        except KeyErrorNotFound:
            logger.warning(f"No source footprint found for '{target_addr}'")
            return ZERO

        sub_pos = sub_fp.at
        offset = kicad.geo.sub(top_pos, sub_pos)

        # TODO rotation?
        return kicad.pcb.Xy(x=offset.x, y=offset.y)

    def _clean_room(self, room_name: str):
        """Delete a room's intra-room-net copper before a re-pull, so routes are
        replaced rather than duplicated (BACKLOG §C3, pinned by C3.9).

        A net is *intra-room* iff every pad referencing it sits on this room's
        footprints. Inter-room nets (pads in two rooms) and a sibling room's nets
        are NOT deleted — this is strictly safer than the old membership-based
        clean, which deleted whatever a group happened to list. Footprints are
        never deleted (they are repositioned by the pull)."""
        pcb = self.pcb
        room_fp_uuids = {
            fp.uuid for fp in pcb.footprints if fp.sheetname == room_name
        }
        if not room_fp_uuids:
            return

        inside: set[int] = set()
        outside: set[int] = set()
        for fp in pcb.footprints:
            bucket = inside if fp.uuid in room_fp_uuids else outside
            for pad in fp.pads:
                if pad.net is not None and pad.net.number != 0:
                    bucket.add(pad.net.number)
        intra = inside - outside

        for container, name in [
            (pcb.segments, "segments"),
            (pcb.arcs, "arcs"),
            (pcb.vias, "vias"),
            (pcb.zones, "zones"),
        ]:
            kicad.filter(pcb, name, container, lambda x: x.net not in intra)

    def pull_room_layout(self, room_name: str):
        """Pull layout for a specific room from its source file (BACKLOG §C3)."""
        if room_name not in self.rooms:
            logger.warning(f"No layout map found for room {room_name}")
            return

        fps = self.rooms[room_name]
        pcb_address = fps[0][1].pcb_address

        top_pcb = self.pcb
        try:
            sub_pcb = self._get_pcb(pcb_address)
        except Exception as e:
            logger.error(f"Error loading sub pcb {pcb_address}: {e}")
            return

        offset = self._calculate_room_offset(sub_pcb, room_name)
        inverted_addr_map = {
            sub_addr.module_address: not_none(self._get_footprint_addr(fp))
            for fp, sub_addr in fps
        }

        net_map = self._generate_net_map(sub_pcb, top_pcb, inverted_addr_map)

        # remove intra-room copper from involved rooms before re-adding (clean by
        # net, not by group membership which no longer exists).
        involved_rooms = {
            self._get_room_name(addr, fp)
            for fp, _ in fps
            for addr in self._get_all_sub_addresses(fp)
        }
        for r_name in involved_rooms:
            self._clean_room(r_name)

        new_fps = self._sync_footprints(
            sub_pcb, top_pcb, inverted_addr_map, net_map, offset
        )

        new_routes = self._sync_routes(sub_pcb, top_pcb, net_map, offset)
        new_other = self._sync_other(sub_pcb, top_pcb, offset)

        # Insert everything. No group membership to update — room identity rides
        # on each footprint's sheetname (set by sync_rooms), so the old
        # member-sort determinism guard (A1/A3) is obsolete.
        for new_element in new_fps + new_routes + new_other:
            container, container_name = PCB_Transformer.get_pcb_container(
                new_element, top_pcb
            )
            kicad.insert(top_pcb, container_name, container, new_element)

    def _get_net_number(self, pcb: PCB, net_name: str) -> int:
        """Resolve a net name to its (file-local) number on `pcb`.

        Callers only pass names taken from the generated net map, which are
        real net names on the target board (and never ""), so a miss means the
        map and the board have desynced. The old behavior returned 0 ("no
        net"), silently disconnecting copper; P0.2 S6b made it fail loudly. The
        empty net "" still resolves normally — it is a real entry (number 0),
        not the unknown case."""
        for net in pcb.nets:
            if net.name == net_name:
                return net.number
        raise KeyError(
            f"net {net_name!r} not found on board; available nets: "
            f"{sorted(n.name for n in pcb.nets)}"
        )
