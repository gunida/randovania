import copy
import dataclasses
from enum import Enum
from typing import Dict, Iterator, Tuple

from randovania.bitpacking import bitpacking
from randovania.bitpacking.bitpacking import BitPackValue, BitPackDecoder, BitPackEnum
from randovania.game_description.resources.translator_gate import TranslatorGate


class LayoutTranslatorRequirement(BitPackEnum, Enum):
    VIOLET = "violet"
    AMBER = "amber"
    EMERALD = "emerald"
    COBALT = "cobalt"
    RANDOM = "random"

    @property
    def item_index(self) -> int:
        if self == LayoutTranslatorRequirement.RANDOM:
            raise ValueError("The random Requirement shouldn't be used for item_index")
        return ITEM_INDICES[self]

    @property
    def long_name(self) -> str:
        return LONG_NAMES[self]


ITEM_INDICES = {
    LayoutTranslatorRequirement.VIOLET: 97,
    LayoutTranslatorRequirement.AMBER: 98,
    LayoutTranslatorRequirement.EMERALD: 99,
    LayoutTranslatorRequirement.COBALT: 100,
}

LONG_NAMES = {
    LayoutTranslatorRequirement.VIOLET: "Violet Translator",
    LayoutTranslatorRequirement.AMBER: "Amber Translator",
    LayoutTranslatorRequirement.EMERALD: "Emerald Translator",
    LayoutTranslatorRequirement.COBALT: "Cobalt Translator",
    LayoutTranslatorRequirement.RANDOM: "Random",
}


@dataclasses.dataclass(frozen=True)
class TranslatorConfiguration(BitPackValue):
    translator_requirement: Dict[TranslatorGate, LayoutTranslatorRequirement]

    def bit_pack_encode(self) -> Iterator[Tuple[int, int]]:
        from randovania.layout import configuration_factory
        templates = [
            configuration_factory.get_vanilla_actual_translator_configurations(),
            configuration_factory.get_vanilla_colors_translator_configurations(),
            TranslatorConfiguration.full_random(),
            self,
        ]

        yield from bitpacking.pack_array_element(self, templates)
        if templates.index(self) == 3:
            for translator in self.translator_requirement.values():
                yield from translator.bit_pack_encode()

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder) -> "TranslatorConfiguration":
        from randovania.layout import configuration_factory
        templates = [
            configuration_factory.get_vanilla_actual_translator_configurations(),
            configuration_factory.get_vanilla_colors_translator_configurations(),
            TranslatorConfiguration.full_random(),
            None,
        ]

        template = decoder.decode_element(templates)
        if template is not None:
            return template

        translator_requirement = {}
        for gate in templates[0].translator_requirement.keys():
            translator_requirement[gate] = LayoutTranslatorRequirement.bit_pack_unpack(decoder)

        return cls(translator_requirement)

    @property
    def as_json(self) -> dict:
        default = TranslatorConfiguration.default()

        return {
            "translator_requirement": {
                str(key.index): item.value
                for key, item in self.translator_requirement.items()
                if item != default.translator_requirement[key]
            }
        }

    @classmethod
    def from_json(cls, value: dict) -> "TranslatorConfiguration":
        default = cls.default()

        translator_requirement = copy.copy(default.translator_requirement)
        for key, item in value["translator_requirement"].items():
            translator_requirement[TranslatorGate(int(key))] = LayoutTranslatorRequirement(item)

        return cls(translator_requirement)

    @classmethod
    def default(cls) -> "TranslatorConfiguration":
        from randovania.layout import configuration_factory
        return configuration_factory.get_vanilla_actual_translator_configurations()

    @classmethod
    def vanilla_colors(cls) -> "TranslatorConfiguration":
        from randovania.layout import configuration_factory
        return configuration_factory.get_vanilla_colors_translator_configurations()

    @classmethod
    def full_random(cls) -> "TranslatorConfiguration":
        default = cls.default()
        return cls({
            key: LayoutTranslatorRequirement.RANDOM
            for key in default.translator_requirement.keys()
        })

    def replace_requirement_for_gate(self, gate: TranslatorGate,
                                     requirement: LayoutTranslatorRequirement,
                                     ) -> "TranslatorConfiguration":
        """
        Replaces the requirement for the given gate. The gate must already have a requirement.
        :param gate:
        :param requirement:
        :return:
        """
        assert gate in self.translator_requirement
        new_requirement = copy.copy(self.translator_requirement)
        new_requirement[gate] = requirement
        return TranslatorConfiguration(new_requirement)
