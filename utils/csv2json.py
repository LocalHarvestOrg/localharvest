import pandas as pd
import json
from pathlib import Path

"""
Convert CSV data to the following JSON format for each document page:

{ 
  "company": {
    "name": "", # name of the company
    "address": "", # address of the company
    "country": "" # country where the company is registered
  },
  "role_holders": [
    {
      "name": "", # full name of the roleholder, can be an individual or an entity
      "address": "", #  address of the roleholder, including street, city and postal code
      "country": "", # the country of the roleholder address
      "nationality": "", # nationality of the roleholder if present
      "role": "", # role of the individual or entity (one of [Shareholder, Director, Owner])
    },
    # continues for each role holder
    # ...
  ]
}

"""
def create_company_entity(name, address, country):
    if not name:  # Skip if no company name
        return None
    
    return {
        "name": name if name==name else None,
        "address": address if address==address else None,
        "country": country if country==country else None
    }

def create_role_holder_entity(name, address, country, nationality, role):
    """Create a role holder entity in FTM format"""
    if not name:  # Skip if no role holder name
        return None
    
    return {
            "name": name if name==name else None,
            "address": address if address==address else None,
            "country": country if country==country else None,
            "nationality": nationality if nationality==nationality else None,
            "role": role if role==role else None  # Role will be set in the relationship creation
    }


def convert_csv_to_ftm(csv_path: Path, output_folder: Path):
    """
    Convert CSV data to Follow The Money (FTM) format
    
    Args:
        csv_path: Path to the CSV file
        output_folder: Path to the folder where JSON files will be saved
    """
    # Create output folder if it doesn't exist
    output_folder.mkdir(parents=True, exist_ok=True)
    
    # Read CSV file
    df = pd.read_csv(csv_path)  # Skip the first row as it contains headers
    
    # Group by document to process each document separately
    grouped = df.groupby('Document')
    
    for document, group in grouped:
        if not document or pd.isna(document):
            continue
            
        entities = {}
        role_holders = []
        for _, row in group.iterrows():
            # Create company entity if not already created
            if pd.notna(row['company_name']):
                entities["company"] = create_company_entity(
                    row['company_name'],
                    row['company_address'],
                    row['company_country']
                )
            # Create person entity if not already created
            if pd.notna(row['relation_name']):
                person = create_role_holder_entity(
                    row['relation_name'],
                    row['relation_address'],
                    row['relation_country'],
                    row['relation_nationality'],
                    row['relationship']
                )
                role_holders.append(person)
        entities["role_holders"] = role_holders

        
        # Save to JSON file
        if entities:
            output_file = output_folder / f"{Path(document).stem}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({"entities": entities}, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    csv_path = Path("/home/ubuntu/data/template_extraction_0/Template_KYC_Manual - Annotation.csv")
    output_folder = Path("/home/ubuntu/data/template_extraction_0/json_output_ground_truth")
    convert_csv_to_ftm(csv_path, output_folder)
