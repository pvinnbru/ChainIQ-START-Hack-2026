import json
from rule_ingestion_prompt import ingest_rule

SAMPLE_RULE = {
    "threshold_id": "AT-007",
    "currency": "CHF",
    "min_amount": 25000,
    "max_amount": 99999.99,
    "min_supplier_quotes": 2,
    "managed_by": ["business", "procurement"],
    "deviation_approval_required_from": ["Procurement Manager"],
}


def test_ingest_rule_empty_dict_and_actions():
    """
    Real Azure OpenAI call with empty start dict (tuples=[]) and no actions so far.
    """
    result = ingest_rule(tuples=[], json_data=SAMPLE_RULE)

    print("\n--- LLM Result ---")
    print(result)
    print("------------------\n")

    assert result is not None
    assert len(result) > 0


if __name__ == "__main__":
    test_ingest_rule_empty_dict_and_actions()
