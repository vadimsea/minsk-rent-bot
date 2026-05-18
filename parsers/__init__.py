"""
Пакет с парсерами источников.

Каждый парсер должен наследоваться от BaseParser и возвращать объявления
в едином формате (см. BaseParser).
"""

from typing import Dict, Type

from parsers.base import BaseParser
from parsers.realt import RealtParser
from parsers.kufar import KufarParser
from parsers.domovita import DomovitaParser
from parsers.hata import HataParser
from parsers.onliner import OnlinerParser


PARSERS: Dict[str, Type[BaseParser]] = {
    "realt": RealtParser,
    "kufar": KufarParser,
    "domovita": DomovitaParser,
    "hata": HataParser,
    "onliner": OnlinerParser,
}


def get_parser(parser_name: str) -> Type[BaseParser] | None:
    return PARSERS.get(parser_name)
