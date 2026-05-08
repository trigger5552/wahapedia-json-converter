from typing import Final


class IdPrefix:
    FACTION: Final = "fac_"
    STRATAGEM: Final = "strat_"
    ABILITY: Final = "abil_"
    DETACHMENT: Final = "det_"
    ENHANCEMENT: Final = "enh_"
    UNIT: Final = "unit_"
    SOURCE: Final = "src_"
    KEYWORD: Final = "key_"

class AbilityType:
    UNIT: Final = "unit_ability"
    FACTION: Final = "fac_ability"
    DETACHMENT: Final = "det_ability"
    GLOBAL: Final = "global_ability"