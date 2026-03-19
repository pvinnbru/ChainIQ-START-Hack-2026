import json
import os
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()


# ===== Azure OpenAI Config (aus .env) =====
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = os.getenv("API_VERSION", "2024-12-01-preview")

# Initialize Client
client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,  
    api_version=API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

DATA_DIR = r"c:\Users\Dimka\OneDrive\Desktop\LOS\ChainIQ-START-Hack-2026-\data"

# ===== Test-Modus =====
# True  → nur die ersten 5 Requests verarbeiten (zum schnellen Testen)
# False → alle Requests verarbeiten
TEST_MODE = True

def load_requests_from_json(limit=None):
    req_file = os.path.join(DATA_DIR, "requests.json")
    if not os.path.exists(req_file):
        print(f"File not found: {req_file}")
        return []

    with open(req_file, 'r', encoding='utf-8') as f:
        all_requests = json.load(f)

    return all_requests[:limit] if limit is not None else all_requests

def extract_fields_with_llm(request_text: str) -> dict:
    """
    Uses Azure OpenAI to extract the required JSON keys from the given request text.
    """
    system_prompt = """
    You are an AI Sourcing Agent. Your task is to extract procurement requirements from the given request text.
    You must output ONLY valid JSON containing the following keys. If a value is not mentioned in the text, use null (or empty array for lists).
    Return ONLY JSON, no markdown formatting blocks.

    Keys to extract:
    - "currency" (e.g. EUR, USD, CHF)
    - "budget_amount" (number)
    - "quantity" (number)
    - "unit_of_measure" (string)
    - "required_by_date" (YYYY-MM-DD)
    - "preferred_supplier_mentioned" (string)
    - "incumbent_supplier" (string)
    - "delivery_countries" (array of standard 2-letter country codes)
    - "detected_language" (string, e.g. "English", "German", "French")
    - "text_output" (string, the full request text translated into English. If it is already in English, return the original text directly)
    """

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract fields from this request:\n\n{request_text}"}
            ]
        )
        
        result_text = response.choices[0].message.content
        return json.loads(result_text)
    
    except Exception as e:
        error_msg = str(e)
        print(f"Error during LLM extraction: {error_msg}")
        return {"llm_error": error_msg}

def determine_category_with_llm(request_text: str, categories_csv_text: str) -> dict:
    """
    Uses Azure OpenAI to determine category_l1 and category_l2 based on the request text and categories CSV.
    """
    system_prompt = f"""
    You are an AI Sourcing Agent. Your task is to categorize the given request text into 'category_l1' and 'category_l2'.
    You must choose the category whose 'category_description' best matches the request.
    Use this CSV data as the only valid categories:

    {categories_csv_text}

    You must output ONLY valid JSON containing the keys "category_l1" and "category_l2".
    Return ONLY JSON, no markdown formatting blocks.
    """

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Determine the category for this request:\n\n{request_text}"}
            ]
        )
        
        result_text = response.choices[0].message.content
        return json.loads(result_text)
    
    except Exception as e:
        error_msg = str(e)
        print(f"Error during LLM category extraction: {error_msg}")
        return {"category_l1": None, "category_l2": None, "llm_error_category": error_msg}

def test_llm_extractor():
    categories_csv_path = os.path.join(DATA_DIR, "categories.csv")
    with open(categories_csv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        categories_csv_text = ""
        for line in lines:
            parts = line.strip().split(',')
            if len(parts) >= 3:
                categories_csv_text += ",".join(parts[:3]) + "\n"

    limit = 5 if TEST_MODE else None
    requests = load_requests_from_json(limit=limit)
    print(f"Testing LLM Extractor on {len(requests)} requests from data/requests/:\n")
    
    output_results = []
    
    for req in requests:
        req_id = req.get('request_id', 'Unknown')
        req_text = req.get('request_text', '')
        
        print("=" * 60)
        print(f"Processing Request ID: {req_id}...")
        
        extracted_data = extract_fields_with_llm(req_text)
        category_data = determine_category_with_llm(req_text, categories_csv_text)
        
        # Behalte alle Metadaten bei, aber leere die Felder, die das LLM neu extrahieren soll
        result_dict = dict(req)
        
        fields_to_clear = [
            "currency", "budget_amount", "quantity", 
            "unit_of_measure", "required_by_date", 
            "preferred_supplier_mentioned", "incumbent_supplier", 
            "delivery_countries", "category_l1", "category_l2"
        ]
        for tf in fields_to_clear:
            if tf in result_dict:
                del result_dict[tf]

        result_dict["original_text"] = req_text
        
        # Füge die aus Azure OpenAI erhaltenen Keys auf derselben Ebene hinzu
        if isinstance(extracted_data, dict):
            result_dict.update(extracted_data)
        if isinstance(category_data, dict):
            result_dict.update(category_data)
        
        output_results.append(result_dict)
        
    # Speichere alle Resultate in eine JSON-Datei
    output_file = "llm_output_samples.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_results, f, indent=4, ensure_ascii=False)
        
    print(f"\nFertig! Die extrahierten Daten wurden in '{output_file}' gespeichert.")

if __name__ == "__main__":
    if not AZURE_OPENAI_API_KEY:
        print("Achtung: AZURE_OPENAI_API_KEY fehlt in der .env Datei!")
    else:
        test_llm_extractor()
        