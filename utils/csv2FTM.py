import pandas as pd
import json
from pathlib import Path
import uuid

def generate_id():
    """Generate a unique ID for FTM entities"""
    return str(uuid.uuid4())

def create_company_entity(name, address, country):
    """Create a company entity in FTM format"""
    if not name:  # Skip if no company name
        return None
    
    return {
        "id": generate_id(),
        "schema": "Company",
        "properties": {
            "name": [name] if name else [],
            "address": [address] if address else [],
            "country": [country] if country else []
        }
    }

def create_person_entity(name, address, country, nationality):
    """Create a person entity in FTM format"""
    if not name:  # Skip if no person name
        return None
    
    return {
        "id": generate_id(),
        "schema": "Person",
        "properties": {
            "name": [name] if name else [],
            "address": [address] if address else [],
            "country": [country] if country else [],
            "nationality": [nationality] if nationality else []
        }
    }

def create_relationship(source_id, target_id, relationship_type):
    """Create a relationship entity in FTM format"""
    relationship_schemas = {
        "shareholder": "Ownership",
        "director": "Directorship",
        "secretary": "Employment",
        "beneficial_owner": "Ownership"
    }
    
    schema = relationship_schemas.get(relationship_type.lower(), "UnknownLink")
    
    return {
        "id": generate_id(),
        "schema": schema,
        "properties": {
            "owner": [source_id] if schema == "Ownership" else [],
            "asset": [target_id] if schema == "Ownership" else [],
            "director": [source_id] if schema == "Directorship" else [],
            "organization": [target_id] if schema in ["Directorship", "Employment"] else [],
            "employee": [source_id] if schema == "Employment" else [],
            "role": [relationship_type] if relationship_type else []
        }
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
            
        ftm_entities = []
        entity_ids = {}  # Store IDs to avoid duplicates
        
        for _, row in group.iterrows():
            # Create company entity if not already created
            company_key = f"company_{row['company_name']}"
            if company_key not in entity_ids and not pd.isna(row['company_name']):
                company = create_company_entity(
                    row['company_name'],
                    row['company_address'],
                    row['company_country']
                )
                if company:
                    ftm_entities.append(company)
                    entity_ids[company_key] = company['id']
            
            # Create person entity if not already created
            person_key = f"person_{row['relation_name']}"
            if person_key not in entity_ids and not pd.isna(row['relation_name']):
                person = create_person_entity(
                    row['relation_name'],
                    row['relation_address'],
                    row['relation_country'],
                    row['relation_nationality']
                )
                if person:
                    ftm_entities.append(person)
                    entity_ids[person_key] = person['id']
            
            # Create relationship if both entities exist
            if (not pd.isna(row['relation_name']) and 
                not pd.isna(row['company_name']) and 
                not pd.isna(row['relationship'])):
                person_id = entity_ids.get(person_key)
                company_id = entity_ids.get(company_key)
                
                if person_id and company_id:
                    relationship = create_relationship(
                        person_id,
                        company_id,
                        row['relationship']
                    )
                    ftm_entities.append(relationship)
        
        # Save to JSON file
        if ftm_entities:
            output_file = output_folder / f"{Path(document).stem}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({"entities": ftm_entities}, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    csv_path = Path("/home/ubuntu/data/template_extraction_0/Template_KYC_Manual - Annotation.csv")
    output_folder = Path("/home/ubuntu/data/template_extraction_0/FTM_output_ground_truth")
    convert_csv_to_ftm(csv_path, output_folder)
