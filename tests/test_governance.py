from app.governance import mask_pii


def test_pii_masking_redacts_sin_and_account():
    raw_payload = {
        "member_id": 1,
        "sin": "123-456-789",
        "email": "jane.doe@example.ca",
        "account_id": 987654321,
        "notes": "Client SIN is 987 654 321",
    }

    masked = mask_pii(raw_payload)

    assert masked["sin"] == "[MASKED]"
    assert masked["email"] == "[MASKED]"
    assert masked["account_id"] == "****4321"
    assert "[REDACTED-SIN]" in masked["notes"]