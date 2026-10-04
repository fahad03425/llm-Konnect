"""Conservative source-domain checks at import boundaries, before mapping."""
import re
import pandas as pd


def pharmacy_evidence(frame: pd.DataFrame) -> bool:
    headers = {re.sub(r'[^a-z0-9]', '', str(c).casefold()) for c in frame.columns}
    explicit = {'genericname', 'medicinename', 'drugname', 'drapregno', 'pharmacistname',
                'prescriptionref', 'prescriptionrequired', 'activeingredient', 'dosageform',
                'dosage', 'formulation', 'strength', 'potency', 'shelflifefield', 'loosepurchase'}
    if headers & explicit:
        return True
    identity = [c for c in frame.columns if re.search(r'product|medicine|drug|item|description|category|name', str(c), re.I)]
    pattern = (
        r'\b(?:paracetamol|acetaminophen|ibuprofen|amoxicillin|amoxil|aspirin|panadol|brufen|augmentin|'
        r'metformin|insulin|cetirizine|omeprazole|cataflam|flagyl|glucophage|losar|neurobion|rigix|'
        r'disprin|calpol|ponstan|gravinate|gaviscon|klaricid|cipro|ciprofloxacin|azithromycin|cefixime|'
        r'pharmaceutical|pharma|medicine|medicines|medical|medication|drug|drugs|pharmacy|antibiotic|'
        r'antibiotics|surgical|prescription|syrup|syrups|tablets?|capsules?|suspension|injection|'
        r'inhaler|ointment|drops?|cough\s+syrup)\b|'
        r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|ug|gm?|ml|iu)\b.*?\b(?:tabs?|tablets?|caps?|capsules?|inj(?:ection)?|syps?|syrup|susp(?:ension)?|drops?|cream|gel|oint(?:ment)?|lotion|spray|vial|amp(?:oule)?)\b|'
        r'\b(?:tabs?|tablets?|caps?|capsules?|inj(?:ection)?|syps?|syrup|susp(?:ension)?|drops?)\b.*?\b\d+(?:\.\d+)?\s*(?:mg|mcg|ug|gm?|ml|iu)\b'
    )
    return any(frame[c].dropna().astype(str).str.contains(pattern, case=False, regex=True).any() for c in identity)



def require_source_domain(frame: pd.DataFrame, domain: str, pharmacy_context: bool = False) -> None:
    if domain != 'pharmacy' or frame.empty:
        return
    identity = [c for c in frame.columns if re.search(r'product|medicine|drug|item|description|category', str(c), re.I)]
    unrelated = r'\b(?:laptop|smartphone|headphones|earbuds|wireless mouse|gaming console|t-shirt|jeans|sneakers|furniture)\b'
    if any(frame[c].dropna().astype(str).str.contains(unrelated, case=False, regex=True).any() for c in identity):
        raise ValueError('This dataset contains non-pharmacy products. Select the appropriate domain.')
    if not pharmacy_context and not pharmacy_evidence(frame):
        raise ValueError('Pharmacy relevance could not be established from this source. Use a pharmacy export with medicine, prescription or registration fields, or select another domain.')


def database_pharmacy_context(connector) -> bool:
    """Allow related lookup/financial tables only when the actual database has pharmacy evidence."""
    for table in connector.list_tables():
        sample = connector.preview(n=25, table_or_query=table)
        if pharmacy_evidence(sample):
            return True
    return False
