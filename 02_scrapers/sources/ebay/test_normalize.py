import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[3]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from scraper.sources2.ebay.normalize import normalize_item, parse_gb, parse_ram


def test_storage():
    assert parse_gb("256 Go") == 256
    assert parse_gb("1 To") == 1024


def test_ram():
    assert parse_ram("8 Go") == 8


def test_normalize():
    item = {
        "itemId": "v1|123|0",
        "title": "Apple iPhone 15 Pro 256 Go Bleu",
        "price": {"value": "699.99", "currency": "EUR"},
        "condition": {
            "conditionId": "1000",
            "conditionDisplayName": "Neuf",
        },
        "localizedAspects": [
            {"name": "Brand", "value": "Apple"},
            {"name": "Model", "value": "iPhone 15 Pro"},
            {"name": "Storage Capacity", "value": "256 GB"},
            {"name": "Color", "value": "Blue"},
        ],
        "categories": [
            {"categoryId": "9355", "categoryName": "Cell Phones & Smartphones"}
        ],
        "itemWebUrl": "https://www.ebay.fr/itm/example",
    }

    row = normalize_item(item)

    assert row["brand"] == "Apple"
    assert row["model"] == "iPhone 15 Pro"
    assert row["storage_gb"] == 256
    assert row["color"] == "Blue"
    assert row["condition_class"] == "NEW"


if __name__ == "__main__":
    test_storage()
    test_ram()
    test_normalize()
    print("Tous les tests unitaires eBay normalize sont validés avec succès !")
