import json
import os
import re
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=os.environ["AZURE_OPENAI_API_VERSION"],
)

SYSTEM_PROMPT = """Your task is to convert procurement rules and modifiers of any kind into a structured, executable action format.

**Inputs**

You receive three inputs:
1. A **dictionary schema** — a list of three-tuples `(key, semantic description, classification)`. Classifications are:
   - `fix_in`: externally provided values the system reads — these are never written to
   - `fix_out`: output values the system must produce — these define what is relevant
   - `meta`: ignore these entries entirely
   - `free`: entries you may add
2. A **rule** as a JSON string.
3. A set of **already-decided actions** for context.

---

**Step 1 — Key mapping**

For each JSON field in the rule, find the matching dict key by direct name or semantic equivalence. Only fields that contribute to a `fix_out` value are relevant. Discard all others and state why. Never represent list-valued fields as dict entries — if a list field is semantically relevant, extract only the scalar aspect that serves a `fix_out`. Output your mapping explicitly before proceeding.

---

**Step 2 — Actions**

Represent each rule effect as one action. Every action has the following tuple format:

`(TYPE, in_param1, in_param2_or_immediate, operator, out_param [, WHEN condition])`

- `in_param1`, `in_param2`: dict keys used as inputs; use `_` if unused
- `in_param2_or_immediate`: either a dict key (AL) or a literal constant (ALI)
- `operator`: `+` `-` `*` `/` `=` `AND` `OR` `XOR` `>=` `<=` etc.
- `out_param`: the dict key that receives the result
- `WHEN condition`: optional boolean expression over dict keys and literals (e.g. `WHEN amount >= 25000 AND currency = CHF`). The action is only applied when this evaluates to true. Omit if the action is unconditional.

**Types:**
1. **AL** — `in_param1 operator in_param2 → out_param`
2. **ALI** — `in_param1 operator immediate → out_param`
3. **OSLM** (Ordered Supplier List Modification) — applies AL or ALI across all supplier matrix entries matching a selection. The WHEN condition may only reference attributes at a strictly higher level (lower level number) than the target attribute.
4. **SRM** (Supplier Rank Modification) — identical to OSLM but `out_param` is always `rank`. Use when the rule affects supplier ordering priority.

When generating actions, order them such that any parameter read by an action is written by a preceding action where possible. Prefer this ordering explicitly.

---

**Step 3 — New dict entries**

If you need dict keys that don't exist, define the minimum set required. Assign a `level` integer to all `free` entries where **0 is the highest/most independent level** and higher numbers indicate dependency on lower-level attributes. Infer level from semantic dependencies — an attribute that logically depends on another attribute must have a strictly higher level number. For supplier matrix entries, additionally tag them as such. If level is ambiguous, assign the lowest plausible level.

---

**Output** — always return all four sections:
```
MAPPING: { json_key → dict_key | DISCARDED: <reason>, ... }

ACTIONS: { (TYPE, in_param1, in_param2/immediate, operator, out_param [, WHEN condition]), ... }

DICT: { (key, semantic description, "free", level [, "supplier_matrix"]), ... }

ATTRIBUTION: { 0: {"rule_id": "<id_field_from_rule_json>", "rule_description": "<brief description of what this action computes>"}, 1: {...}, ... }
```

Where each ATTRIBUTION index corresponds to the 0-based position in the ACTIONS list.
Use the primary identifier field from the rule JSON (e.g., `threshold_id`, `rule_id`, `category_rule_id`, or similar) as the `rule_id`.
The `rule_description` should briefly describe what the action computes or enforces.

If no new dict entries are needed, return `DICT: {}`."""

def parse_rule_attribution(llm_output: str) -> dict:
    """
    Extract the ATTRIBUTION block from an LLM response for rule ingestion.

    Expected format in the response::

        ATTRIBUTION: {
          0: {"rule_id": "AT-001", "rule_description": "Set min_supplier_quotes for low amounts"},
          1: {"rule_id": "AT-001", "rule_description": "Gate fast-track eligibility"},
        }

    Returns a dict mapping int action_index → {"rule_id": str, "rule_description": str}.
    Returns an empty dict if the block is absent or unparseable.
    """
    attr_start = llm_output.find("ATTRIBUTION:")
    if attr_start == -1:
        return {}

    brace_start = llm_output.find("{", attr_start)
    if brace_start == -1:
        return {}

    # Find the matching closing brace using depth tracking
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(llm_output)):
        if llm_output[i] == "{":
            depth += 1
        elif llm_output[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break
    else:
        return {}

    block = llm_output[brace_start : brace_end + 1]

    result: dict = {}
    # Each entry: integer_key: {"rule_id": "...", "rule_description": "..."}
    for m in re.finditer(r"(\d+)\s*:\s*\{([^}]+)\}", block):
        idx = int(m.group(1))
        inner = m.group(2)
        rid_m = re.search(r'"rule_id"\s*:\s*"([^"]*)"', inner)
        rdesc_m = re.search(r'"rule_description"\s*:\s*"([^"]*)"', inner)
        if rid_m and rdesc_m:
            result[idx] = {
                "rule_id": rid_m.group(1),
                "rule_description": rdesc_m.group(1),
            }

    return result


def ingest_rule(
    tuples: list[tuple],
    json_data: dict,
    actions_so_far: list[tuple] | None = None,
) -> str:
    tuples_str = "\n".join(f"  {t}" for t in tuples)
    json_str = json.dumps(json_data, indent=2)

    if actions_so_far:
        actions_str = "\n".join(
            f"  ({', '.join(str(x) for x in a)})" for a in actions_so_far
        )
    else:
        actions_str = "  (none)"

    user_message = (
        f"### Input Dictionary Schema\n{tuples_str}\n\n"
        f"### JSON Data\n```json\n{json_str}\n```\n\n"
        f"### Actions so far\n{actions_str}"
    )

    response = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    sample_tuples = [
        ("rule_id_1", "condition_A", "action_X"),
        ("rule_id_2", "condition_B", "action_Y"),
        ("rule_id_3", "condition_C", "action_Z"),
    ]

    # Load from file or define inline
    json_path = "rules_config.json"
    if os.path.exists(json_path):
        with open(json_path) as f:
            sample_json = json.load(f)
    else:
        sample_json = {"example_key": "example_value"}

    result = ingest_rule(sample_tuples, sample_json)
    print(result)
