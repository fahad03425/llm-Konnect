"""
Test all 200 Roman Urdu questions against PharmacyPOS dataset
"""
import os
import sys
from datetime import date, timedelta
import pandas as pd

sys.path.insert(0, ".")

from app.language.roman_urdu import normalize_roman_urdu_intent
from app.rag.router import classify_route, extract_filters, RouteType
from app.analytics.seam import AnalyticsRouter
from app.api.analytics import _load_canonical, KPIRequest

QUESTIONS = [
    # 1–40: Sales — Common Roman Urdu
    "Aaj ki sale kitni hui?",
    "Aj mene kitne ki sale ki?",
    "Aaj total kitni sales hui hain?",
    "Aj ka total sale btao.",
    "Aaj ki kamai kitni hai?",
    "Kal ki sale kitni thi?",
    "Kal mene kitne ki sale ki thi?",
    "Kal ka total sale batao.",
    "Parso kitni sale hui thi?",
    "Parso ki kamai btao.",
    "Is hafte kitni sale hui?",
    "Pichle hafte ki sale batao.",
    "Is month ki total sale kya hai?",
    "Is mahine kitni sale hui hai?",
    "Pichle mahine kitni sale hui thi?",
    "Is saal ki total sale batao.",
    "Pichle 7 din ki sale btao.",
    "Pichle 10 din mein kitni sale hui?",
    "Pichle 30 din ki sale kya hai?",
    "Aaj kitne bill bane?",
    "Kal kitne bills bane thay?",
    "Is mahine kitne bills banay hain?",
    "Aaj ka sab se bara bill konsa tha?",
    "Aaj ki sab se choti sale kitni thi?",
    "Aaj sab se zyada sale kab hui?",
    "Kis din sab se zyada sale hui?",
    "Kis din sab se kam sale hui?",
    "Meri daily average sale kitni hai?",
    "Rozana ausatan kitni sale hoti hai?",
    "Is month ki average sale btao.",
    "Sale barh rahi hai ya kam ho rahi hai?",
    "Meri sales ka haal kaisa hai?",
    "Pichle month aur is month ki sale compare kro.",
    "Kal aur aaj ki sale mein kitna farq hai?",
    "Is hafte aur pichle hafte ki sale compare karo.",
    "Meri sale kitne percent barhi hai?",
    "Meri sale kitne feesad kam hui hai?",
    "Sab se achi sale kis din hui thi?",
    "Pichle 3 mahino ki sale dikhao.",
    "Meri recent sales ka trend batao.",

    # 41–70: Medicines / Products
    "Sab se zyada bikne wali dawa konsi hai?",
    "Sab se zyada bikne wali medicine batao.",
    "Konsi dawa sab se zyada chal rahi hai?",
    "Aaj sab se zyada konsi medicine biki?",
    "Is mahine konsi dawa sab se zyada biki?",
    "Top 5 medicines batao.",
    "Top 10 bikne wali dawaiyan dikhao.",
    "Sab se kam bikne wali medicine konsi hai?",
    "Konsi dawa bilkul nahi bik rahi?",
    "Konsi dawaiyan slow chal rahi hain?",
    "Konsi medicines fast chal rahi hain?",
    "Brufen kitni biki hai?",
    "Brufen 400mg ki sale batao.",
    "Is month Brufen kitni biki?",
    "Brufen se kitni kamai hui?",
    "Panadol ki sale kitni hui?",
    "Caflam kitni sell hui?",
    "Gravinate ki sales btao.",
    "Synflex ki kitni sale hui?",
    "Hydryllin syrup kitna bika?",
    "Kis medicine se sab se zyada paisa aya?",
    "Konsi dawa sab se zyada revenue deti hai?",
    "Konsi medicine ki demand sab se zyada hai?",
    "Kin dawaiyon ki demand barh rahi hai?",
    "Kin medicines ki demand gir rahi hai?",
    "Pichle 30 din se konsi dawa nahi biki?",
    "Pichle 3 mahine se konsi medicines nahi bikin?",
    "Konsi medicine roz bikti hai?",
    "Konsi dawaiyan aksar bikti rehti hain?",
    "Medicine ki sales history dikhao.",

    # 71–105: Stock / Inventory
    "Brufen ka stock kitna hai?",
    "Brufen kitni pari hui hai?",
    "Panadol ki kitni quantity bachi hai?",
    "Konsi medicines stock mein hain?",
    "Konsi dawaiyan khatam ho gai hain?",
    "Konsi medicine out of stock hai?",
    "Kis dawa ka stock kam hai?",
    "Low stock wali medicines batao.",
    "Konsi dawai ki 10 se kam units bachi hain?",
    "Konsi medicines ki sirf 5 units bachi hain?",
    "Kis medicine ka stock sab se zyada hai?",
    "Sab se zyada quantity kis dawa ki pari hai?",
    "Total stock kitna hai?",
    "Mere pas total kitni medicines hain?",
    "Inventory ki total value kitni hai?",
    "Mere stock ki qeemat kitni hai?",
    "Stock mein kitne paisay lage hue hain?",
    "Konsi medicines zyada quantity mein pari hain?",
    "Konsi dawa overstock hai?",
    "Konsi dawai zaroorat se zyada mangwa li hai?",
    "Konsi medicines dobara mangwani chahiye?",
    "Kya order karna chahiye?",
    "Aj konsi medicines mangwaon?",
    "Konsi dawa ka stock jaldi khatam hone wala hai?",
    "Agle hafte konsi medicine khatam ho sakti hai?",
    "Fast moving aur low stock medicines batao.",
    "Konsi medicine stock mein hai magar bik nahi rahi?",
    "Mera paisa kis stock mein phansa hua hai?",
    "Dead stock konsa hai?",
    "Slow moving stock dikhao.",
    "Current inventory ki halat kya hai?",
    "Stock ki surat-e-haal batao.",
    "Kis medicine ko foran restock karna chahiye?",
    "Brufen ka stock kitne din chalega?",
    "Maujooda stock kitne din ke liye kafi hai?",

    # 106–130: Expiry / Batch
    "Konsi medicines expire ho chuki hain?",
    "Konsi dawai ki expiry ho gai hai?",
    "Koi expired stock para hua hai?",
    "Expired medicines ki list do.",
    "Konsi dawa jaldi expire hone wali hai?",
    "Agle 30 din mein kya expire hoga?",
    "Agle mahine konsi dawai expire hogi?",
    "60 din mein expire hone wali medicines batao.",
    "90 din mein konsi medicines expire hongi?",
    "Near expiry stock dikhao.",
    "Qareeb-ul-expiry medicines konsi hain?",
    "Brufen kab expire hogi?",
    "Brufen ki expiry date kya hai?",
    "Brufen ke batches dikhao.",
    "Brufen ka konsa batch pehle expire hoga?",
    "Sab se pehle konsa stock expire hoga?",
    "Kis batch mein sab se zyada stock hai?",
    "Konsi medicine ke multiple batches hain?",
    "Expire hone wale stock ki value kitni hai?",
    "Expiry ki wajah se kitna nuksan ho sakta hai?",
    "Konsi expired medicine abhi tak stock mein mojood hai?",
    "Qareebi muddat mein konsi dawaiyan expire hongi?",
    "Kin dawaiyon ko expiry se pehle bechna zaroori hai?",
    "Expiry ke khatre wala stock batao.",
    "Konsa batch pehle sell karna chahiye?",

    # 131–155: Purchase / Supplier
    "Aaj kitni purchasing hui?",
    "Is mahine kitni purchase ki?",
    "Pichle month kitne ka maal khareeda?",
    "Pichle 30 din ki purchases batao.",
    "Is saal total kitni purchasing hui?",
    "Sab se zyada konsi medicine khareedi?",
    "Brufen last time kab mangwai thi?",
    "Brufen kitne ki khareedi thi?",
    "Brufen ki last purchase price kya thi?",
    "Brufen kis supplier se li thi?",
    "Mera sab se bara supplier kon hai?",
    "Kis supplier se sab se zyada maal liya?",
    "Is month kis supplier se zyada purchase ki?",
    "Har supplier se kitne ka maal liya?",
    "Supplier wise purchase dikhao.",
    "Kis supplier se Brufen milti hai?",
    "Kis supplier se last order aya tha?",
    "Konsa supplier sab se zyada medicines deta hai?",
    "Kin suppliers se recently maal nahi liya?",
    "Konsi medicine ki purchase price barh gai hai?",
    "Kin dawaiyon ke rate barh gaye hain?",
    "Konsi medicine mehngi ho rahi hai?",
    "Purchase rate mein sab se zyada izafa kis ka hua?",
    "Purani aur nai purchase price compare karo.",
    "Kis medicine ki cost kam hui hai?",

    # 156–175: Profit / Business Performance
    "Mera profit kitna hua?",
    "Aaj kitna munafa hua?",
    "Is mahine kitna munafa hua?",
    "Pichle mahine kitna profit hua tha?",
    "Konsi medicine sab se zyada munafa deti hai?",
    "Sab se zyada profit kis dawa se ho raha hai?",
    "Kis product ka margin sab se acha hai?",
    "High margin medicines konsi hain?",
    "Kam margin wali medicines konsi hain?",
    "Konsi medicine bikti zyada hai lekin profit kam deti hai?",
    "Konsi dawa kam bikti hai lekin margin acha hai?",
    "Mere karobar ki performance kesi hai?",
    "Pharmacy ka karobar kaisa chal raha hai?",
    "Kya mera business behtar ho raha hai?",
    "Is mahine karobar mein behtari hui ya kami?",
    "Meri amadni mein kitna izafa hua?",
    "Kis cheez se mujhe sab se zyada faida ho raha hai?",
    "Kis stock ki wajah se mujhe nuksan ho raha hai?",
    "Meri dukaan ka paisa kahan phansa hua hai?",
    "Konsi medicines meri profitability ko kam kar rahi hain?",

    # 176–200: Harder Roman Urdu + Decision Support
    "Main apni sale kaise barha sakta hoon?",
    "Meri bikri barhane ke liye kya karna chahiye?",
    "Karobar ki amadni barhane ka kya tareeqa hai?",
    "Mere data ke mutabiq bikri mein izafa kaise ho sakta hai?",
    "Konsi dawaiyon par zyada tawajjo deni chahiye?",
    "Kin medicines ki farokht barhane ki gunjaish hai?",
    "Konsi dawaiyan meri amadni mein sab se zyada izafa kar sakti hain?",
    "Mujhe konsi medicines zyada miqdaar mein mangwani chahiye?",
    "Kin dawaiyon ki khareedari kam karni chahiye?",
    "Mojooda stock ko dekh kar agla order kya hona chahiye?",
    "Meri zaroorat se zyada mojood stock konsa hai?",
    "Kis stock ko kam karna mere liye behtar hoga?",
    "Kin medicines ki kami ki wajah se meri sale miss ho sakti hai?",
    "Konsi dawai ki dastiyabi yaqini banana zaroori hai?",
    "Mere karobar mein is waqt sab se bara masla kya hai?",
    "Mere pharmacy data se kya aham masail nazar aa rahe hain?",
    "Main apna munafa kis tarah barha sakta hoon?",
    "Main apna nuksan kis tarah kam kar sakta hoon?",
    "Expiry ki wajah se hone wale nuqsan ko kaise roka ja sakta hai?",
    "Kin dawaiyon ko foran farokht karne ki zaroorat hai?",
    "Konsi medicines ki talab zyada aur dastiyabi kam hai?",
    "Kin products ki talab kam hone ke bawajood stock zyada hai?",
    "Meri khareed-o-farokht mein kya behtari ki ja sakti hai?",
    "Maujooda sales aur stock ke mutabiq mujhe kya faisla lena chahiye?",
    "Mere sales, stock, expiry aur purchase data ka jaiza le kar batao ke karobar behtar karne ke liye mujhe kya iqdamat karne chahiye?"
]

def main():
    print("Loading canonical PharmacyPOS database...")
    df, _ = _load_canonical(KPIRequest(file_path="db://PharmacyPOS", domain="pharmacy"))
    if df is None or df.empty:
        print("Falling back to local csv/excel...")
        df = pd.read_csv("data/pharmacy_pos_sales.csv")
    print(f"Loaded DataFrame: {len(df)} rows, columns: {list(df.columns)}")

    router = AnalyticsRouter()
    
    passed_count = 0
    failed = []

    for idx, q in enumerate(QUESTIONS, 1):
        norm = normalize_roman_urdu_intent(q)
        route = classify_route(norm)
        filters = extract_filters(norm, "pharmacy")
        
        # Test compute
        try:
            computed, source_rows = router.compute(norm, filters, df, domain="pharmacy")
        except Exception as e:
            computed = None
            source_rows = []
            err = str(e)
        else:
            err = None

        has_valid_metric = False
        val_summary = ""
        if computed and isinstance(computed, dict):
            ok_items = [v for v in computed.values() if isinstance(v, dict) and v.get("status") == "ok" and v.get("value") is not None]
            if ok_items:
                has_valid_metric = True
                val_summary = ", ".join([f"{item.get('name')}: {item.get('value')} {item.get('unit','')}" for item in ok_items[:2]])
            else:
                val_summary = "All items unavailable/empty"
        else:
            val_summary = f"Error: {err}" if err else "None"

        # Check if question passed
        is_pass = (route == RouteType.ANALYTICS and has_valid_metric)
        if is_pass:
            passed_count += 1
        else:
            failed.append((idx, q, norm, route, filters, val_summary))

    print("\n" + "="*80)
    print(f"SUMMARY: {passed_count}/{len(QUESTIONS)} PASSED, {len(failed)} FAILED")
    print("="*80)
    
    if failed:
        print(f"\nFAILED QUESTIONS ({len(failed)} total):")
        for idx, q, norm, route, filters, val_summary in failed:
            print(f"\n#{idx}: {q}")
            print(f"  Norm   : {norm}")
            print(f"  Route  : {route}")
            print(f"  Filters: {filters}")
            print(f"  Result : {val_summary}")

if __name__ == "__main__":
    main()
