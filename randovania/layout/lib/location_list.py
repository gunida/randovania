from dataclasses import dataclass
from typing import Iterator, Callable, TypeVar, Iterable

from randovania.bitpacking import bitpacking
from randovania.bitpacking.bitpacking import BitPackValue, BitPackDecoder
from randovania.game_description import default_database
from randovania.game_description.world.area_identifier import AreaIdentifier
from randovania.game_description.world.node import Node
from randovania.game_description.world.node_identifier import NodeIdentifier
from randovania.games.game import RandovaniaGame


def _sorted_node_identifiers(elements: Iterable[NodeIdentifier]) -> list[NodeIdentifier]:
    return sorted(elements)

def area_locations_with_filter(game: RandovaniaGame, condition: Callable[[Node], bool]) -> list[NodeIdentifier]:
    world_list = default_database.game_description_for(game).world_list
    identifiers = [
        AreaIdentifier(
            world_name=world.name,
            area_name=area.name,
        )
        for world in world_list.worlds
        for area in world.areas
        if condition(area)
    ]
    return _sorted_node_identifiers(identifiers)

def node_locations_with_filter(game: RandovaniaGame, condition: Callable[[Node], bool]) -> list[NodeIdentifier]:
    world_list = default_database.game_description_for(game).world_list
    identifiers = [
        NodeIdentifier.create(world.name, area.name, node.name)
        for world in world_list.worlds
        for area in world.areas
        for node in area.actual_nodes
        if condition(node)
    ]
    return _sorted_node_identifiers(identifiers)


T = TypeVar("T")
SelfType = TypeVar("SelfType")


@dataclass(frozen=True)
class LocationList(BitPackValue):
    locations: tuple[NodeIdentifier, ...]
    game: RandovaniaGame

    def __post_init__(self):
        if not isinstance(self.locations, tuple):
            raise ValueError(f"locations must be tuple, got {type(self.locations)}")

        if list(self.locations) != sorted(self.locations):
            raise ValueError(f"locations aren't sorted: {self.locations}")

    @classmethod
    def nodes_list(cls, game: RandovaniaGame) -> list[NodeIdentifier]:
        return node_locations_with_filter(game, lambda area: True)

    @classmethod
    def element_type(cls) -> type[NodeIdentifier]:
        return NodeIdentifier

    @classmethod
    def with_elements(cls: type[SelfType], elements: Iterable[NodeIdentifier], game: RandovaniaGame) -> SelfType:
        elements_set = frozenset(elements)
        all_locations = frozenset(cls.nodes_list(game))
        return cls(tuple(sorted(elements_set & all_locations)), game)

    def bit_pack_encode(self, metadata) -> Iterator[tuple[int, int]]:
        nodes = self.nodes_list(self.game)
        yield from bitpacking.pack_sorted_array_elements(list(self.locations), nodes)

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata) -> "LocationList":
        game = metadata["reference"].game
        return cls.with_elements(bitpacking.decode_sorted_array_elements(decoder, cls.nodes_list(game)), game)

    @property
    def as_json(self) -> list[dict]:
        return [location.as_json for location in self.locations]

    @classmethod
    def from_json(cls, value: list[dict], game: RandovaniaGame) -> "LocationList":
        if not isinstance(value, list):
            raise ValueError(f"StartingLocation from_json must receive a list, got {type(value)}")
        elements = [cls.element_type().from_json(location) for location in value]
        return cls.with_elements(elements, game)

    def ensure_has_location(self: SelfType, node_location: NodeIdentifier, enabled: bool) -> SelfType:
        new_locations = set(self.locations)

        if enabled:
            new_locations.add(node_location)
        elif node_location in new_locations:
            new_locations.remove(node_location)

        return self.with_elements(iter(new_locations), self.game)

    def ensure_has_locations(self: SelfType, node_locations: list[NodeIdentifier], enabled: bool) -> SelfType:
        new_locations = set(self.locations)

        for node_location in node_locations:
            if enabled:
                new_locations.add(node_location)
            elif node_location in new_locations:
                new_locations.remove(node_location)

        return self.with_elements(new_locations, self.game)
