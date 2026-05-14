from datetime import datetime

from app.pipeline import _DEFAULT_EXTRACT_BATCH_SIZE, _extract_structured_batches, _limit_recent_messages
from app.extractor import MappingResolver
from app.schemas import ParsedMessage


def test_default_extract_batch_size_is_2000():
    assert _DEFAULT_EXTRACT_BATCH_SIZE == 2000


def test_extract_batches_preserves_input_order():
    messages = [
        ParsedMessage(timestamp=datetime(2026, 5, 14, 10, idx, 0), sender=f"sender-{idx}", message=f"Need 2 bhk in Baner {idx}", source="test", raw_message="")
        for idx in range(3)
    ]

    rows = [["Raw", "Canonical", "Aliases", "Tags"]]
    location_map = MappingResolver(rows)
    property_map = MappingResolver(rows)
    leads = _extract_structured_batches(
        [messages[:1], messages[1:]],
        location_map,
        property_map,
        {},
        total_messages=len(messages),
    )

    assert len(leads) == 3
    assert [lead.values["Raw Message"] for lead in leads] == [msg.message for msg in messages]


def test_limit_recent_messages_keeps_newest_subset_in_order():
    messages = [
        ParsedMessage(timestamp=datetime(2026, 5, 14, 10, idx, 0), sender=f"sender-{idx}", message=str(idx), source="test", raw_message="")
        for idx in range(5)
    ]

    kept, skipped = _limit_recent_messages(messages, 2)

    assert skipped == 3
    assert [message.message for message in kept] == ["3", "4"]
