import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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

**Step 0 — Classify the policy**

Before doing anything else, identify which of the following policy types this rule belongs to:

- `approval_threshold`: Sets minimum supplier quotes and escalation approvers based on spend amount and currency ranges.
- `category_compliance_supplier_gate`: A compliance requirement that, if violated by a supplier, must exclude that supplier from the shortlist entirely (e.g. data residency, restricted geographies).
- `category_compliance_process_gate`: A compliance requirement that blocks the award process until a human step is completed, regardless of which supplier wins (e.g. CV review, security architecture review, design sign-off). Does NOT filter suppliers.
- `category_compliance_soft`: A preference or guideline ("should") that does not exclude suppliers but should influence ranking (e.g. ESG preference, performance baselines).
- `scoring_modifier`: Directly modifies supplier scores or ordering priority.

State the classification explicitly: `POLICY_TYPE: <type>`

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

**IMPORTANT — WHEN conditions are always enforced.** Every action with a WHEN condition is skipped if that condition evaluates to false. This means:
- Every rule that applies only under certain conditions MUST encode those conditions in WHEN.
- WHEN conditions with string comparisons MUST use quoted values: `currency = "EUR"`, `category_l2 = "Cloud Compute"`. Multi-word strings must be quoted as a single value.
- Never put the trigger condition for a rule in the action body — always put it in WHEN.

**Type-specific guidance by policy type:**

`approval_threshold`:
- Each threshold band is a separate action set.
- Template: `ALI(_, <count>, =, min_supplier_quotes, WHEN budget >= <min> AND budget <= <max> AND currency = "<X>")`
- Template: `ALI(_, True, =, escalate_to_<role>, WHEN budget >= <min> AND budget <= <max> AND currency = "<X>")`
- The WHEN condition is mandatory. Never write to min_supplier_quotes or escalation flags without a WHEN.

`category_compliance_supplier_gate`:
- The supplier must be excluded when it violates a hard constraint. Use OSLM to set `excluded = True`.
- The WHEN condition must express when the violation occurs (e.g. request requires data_residency AND supplier does NOT support it).
- Template: `OSLM(_, True, =, excluded, WHEN <request_constraint_flag> = True AND <supplier_capability_flag> = False AND category_l1 = "<L1>" AND category_l2 = "<L2>")`
- Example: `OSLM(_, True, =, excluded, WHEN data_residency_constraint = True AND data_residency_supported = False AND category_l2 = "Cloud Compute")`

`category_compliance_process_gate`:
- This rule does NOT filter suppliers. It sets a boolean process requirement flag.
- Template: `ALI(_, True, =, <requirement_flag>, WHEN category_l1 = "<L1>" AND category_l2 = "<L2>" [AND <additional_condition>])`
- The requirement_flag should be a descriptive key like `requires_cv_review`, `requires_security_review`.

`category_compliance_soft`:
- A "should" preference — supplier is NOT excluded but is penalized in ranking.
- Use OSLM to reduce `compliance_score` when the supplier fails to meet the preference.
- `compliance_score` starts at 1.0 per supplier; subtracting a severity penalty reduces normalized_rank multiplicatively.
- Severity guidance: subtract 0.05 for a minor preference (e.g. performance baseline absent), 0.15 for moderate (e.g. ESG requirement unmet), 0.30 for significant (e.g. missing required certifications that are not strictly mandatory).
- Template: `OSLM(compliance_score, <severity_0.05_to_0.30>, -, compliance_score, WHEN <violation_condition>)`
- Example: `OSLM(compliance_score, 0.15, -, compliance_score, WHEN esg_requirement = True AND esg_compliant = False)`

When generating actions, order them such that any parameter read by an action is written by a preceding action where possible.

---

**Step 3 — New dict entries**

If you need dict keys that don't exist, define the minimum set required. Assign a `level` integer to all `free` entries where **0 is the highest/most independent level** and higher numbers indicate dependency on lower-level attributes. Infer level from semantic dependencies — an attribute that logically depends on another attribute must have a strictly higher level number. For supplier matrix entries, additionally tag them as such. If level is ambiguous, assign the lowest plausible level.

---

**Output** — always return all four sections:
```
POLICY_TYPE: <type>

MAPPING: { json_key → dict_key | DISCARDED: <reason>, ... }

ACTIONS: { (TYPE, in_param1, in_param2/immediate, operator, out_param [, WHEN condition]), ... }

DICT: { (key, semantic description, "free", level [, "supplier_matrix"]), ... }

ATTRIBUTION: { 0: {"rule_id": "<id_field_from_rule_json>", "rule_description": "<brief description of what this action computes>"}, 1: {...}, ... }
```

Where each ATTRIBUTION index corresponds to the 0-based position in the ACTIONS list.
Use the primary identifier field from the rule JSON (e.g., `threshold_id`, `rule_id`, `category_rule_id`, or similar) as the `rule_id`.
The `rule_description` should briefly describe what the action computes or enforces.

If no new dict entries are needed, return `DICT: {}`."""


# ---------------------------------------------------------------------------
# Escalation rule ingestion (separate prompt — these become natural language
# conditions stored outside the actions pipeline)
# ---------------------------------------------------------------------------

MAX_CONCURRENT_LLM_CALLS: int = 10

ESCALATION_SYSTEM_PROMPT = """Your task is to convert procurement escalation rules into structured natural-language trigger conditions.

Escalation rules define WHEN a process should be escalated to a human authority. They are NOT converted into executable actions. Instead, produce a structured record for each rule that captures:
- The trigger condition in clear, unambiguous natural language
- The escalation target (who to escalate to)
- Any constraints that scope the rule (e.g. applies only to certain currencies or categories)

**Input**: A single escalation rule as a JSON object.

**Output** — return exactly this structure:
```
ESCALATION_RULE: {
  "rule_id": "<rule_id from JSON>",
  "trigger_condition": "<natural language description of when this escalation fires, be precise and complete>",
  "escalate_to": "<target role or team>",
  "applies_when": "<optional: scope constraint in natural language, or 'always' if unconditional>"
}
```

Be specific. "Missing required information" is too vague — say "One or more required request fields are absent or ambiguous (e.g. quantity, category, budget, or delivery country)."
Do not invent conditions not present in the rule JSON."""


def ingest_escalation_rules(rules: list[dict]) -> list[dict]:
    """
    Convert a list of escalation rule dicts into structured natural-language
    trigger records.

    All rules are submitted to the LLM in parallel (up to MAX_CONCURRENT_LLM_CALLS
    at once). Each rule is fully independent so parallelism is safe. Results are
    returned in the same order as the input list.

    Returns a list of dicts with keys: rule_id, trigger_condition, escalate_to,
    applies_when. Rules that fail to parse are skipped with a warning.
    """
    def _ingest_one(rule: dict) -> dict | None:
        json_str = json.dumps(rule, indent=2)
        response = client.chat.completions.create(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            messages=[
                {"role": "system", "content": ESCALATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"### Escalation Rule\n```json\n{json_str}\n```"},
            ],
            temperature=0.1,
        )
        return _parse_escalation_rule(response.choices[0].message.content)

    # Submit all rules in parallel, collect by original index to preserve order
    results_by_idx: dict[int, dict | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM_CALLS) as executor:
        future_to_idx = {
            executor.submit(_ingest_one, rule): idx
            for idx, rule in enumerate(rules)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results_by_idx[idx] = future.result()
            except Exception as exc:
                rule_id = rules[idx].get("rule_id", "UNKNOWN")
                print(f"[WARNING] LLM call failed for escalation rule {rule_id}: {exc}")
                results_by_idx[idx] = None

    out: list[dict] = []
    for idx in sorted(results_by_idx):
        parsed = results_by_idx[idx]
        if parsed:
            out.append(parsed)
        else:
            rule_id = rules[idx].get("rule_id", "UNKNOWN")
            print(f"[WARNING] Failed to parse escalation rule {rule_id}")
    return out


def _parse_escalation_rule(llm_output: str) -> dict | None:
    """
    Extract the ESCALATION_RULE JSON block from an LLM response.

    Returns a dict with keys rule_id, trigger_condition, escalate_to,
    applies_when, or None if the block is absent or unparseable.
    """
    marker = llm_output.find("ESCALATION_RULE:")
    if marker == -1:
        return None
    brace_start = llm_output.find("{", marker)
    if brace_start == -1:
        return None

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
        return None

    block = llm_output[brace_start : brace_end + 1]
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        # Attempt field-by-field extraction as fallback
        result: dict = {}
        for field in ("rule_id", "trigger_condition", "escalate_to", "applies_when"):
            m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', block)
            if m:
                result[field] = m.group(1)
        return result if "rule_id" in result else None


# ---------------------------------------------------------------------------
# Action rule ingestion (existing pipeline)
# ---------------------------------------------------------------------------

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
