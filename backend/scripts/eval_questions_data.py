"""Automated Evaluation Script for 100 RAG Chatbot Questions on Pakistan_Pharmaceutical_Products_Pricing_and_Availability_Data.xlsx"""

import json
import time
import requests
import pandas as pd
import numpy as np
import os
from typing import Dict, Any, List

DATASET_PATH = 'C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/data/uploads/Pakistan_Pharmaceutical_Products_Pricing_and_Availability_Data.xlsx'
API_URL = 'http://127.0.0.1:8756/api/chat'
FILE_ID = 'file_ae2e68f26f42'

# Load and prepare dataset
df = pd.read_excel(DATASET_PATH)
df_clean = df.dropna(how='all').copy()

def parse_num(val):
    if pd.isna(val):
        return np.nan
    s = str(val).replace('PKR', '').replace('Rs', '').replace(',', '').replace('%', '').replace('Off', '').strip()
    try:
        return float(s)
    except:
        return np.nan

df_clean['Price_before_clean'] = df_clean['Price_before'].apply(parse_num)
df_clean['Price_After_clean'] = df_clean['Price_After'].apply(parse_num)
df_clean['Discount_pct'] = df_clean['Discount'].apply(parse_num)
df_clean['Savings'] = df_clean['Price_before_clean'] - df_clean['Price_After_clean']

QUESTIONS = [
    # General / Product (1-10)
    (1, "What is the price of Zestril?"),
    (2, "Is Zestril currently available?"),
    (3, "Which company manufactures Zestril?"),
    (4, "What is the pack size of Zestril?"),
    (5, "What was the original price of Zestril?"),
    (6, "What is the discounted price of Zestril?"),
    (7, "How much discount is available on Zestril?"),
    (8, "Show me all available products."),
    (9, "Show me the details of this medicine."),
    (10, "What pack sizes are available for this product?"),
    
    # Price & Discount (11-25)
    (11, "What is the cheapest medicine?"),
    (12, "What is the most expensive medicine?"),
    (13, "Show medicines costing less than PKR 500."),
    (14, "Show medicines costing between PKR 500 and PKR 1,000."),
    (15, "Which medicines cost more than PKR 1,000?"),
    (16, "Which medicines have a discount?"),
    (17, "Which medicine has the highest discount?"),
    (18, "Show me products with a 10% discount."),
    (19, "Which products have no discount?"),
    (20, "Which medicines cost less than PKR 300 after discount?"),
    (21, "How much money do I save on this medicine?"),
    (22, "What is the difference between the original and discounted price?"),
    (23, "Show the top 10 products with the biggest price reductions."),
    (24, "Which expensive medicines currently have discounts?"),
    (25, "What is the average discount across all products?"),
    
    # Availability (26-35)
    (26, "How many products are currently available?"),
    (27, "How many products are unavailable?"),
    (28, "Which medicines are unavailable?"),
    (29, "Show all available medicines under PKR 500."),
    (30, "Which discounted medicines are currently available?"),
    (31, "Is this medicine available?"),
    (32, "Are any products from this company unavailable?"),
    (33, "Which company has the most available products?"),
    (34, "Show available products costing between PKR 500 and PKR 1,000."),
    (35, "What percentage of products are currently available?"),
    
    # Company Analysis (36-50)
    (36, "Show me all medicines from ICI Pakistan Limited."),
    (37, "How many products does ICI Pakistan Limited have?"),
    (38, "What is the cheapest product from this company?"),
    (39, "What is the most expensive product from this company?"),
    (40, "Which products from this company are available?"),
    (41, "Which products from this company have discounts?"),
    (42, "How many pharmaceutical companies are represented in the dataset?"),
    (43, "Which company has the most products?"),
    (44, "Show the top 10 companies by number of products."),
    (45, "What is the average product price for each company?"),
    (46, "Which company has the highest average product price?"),
    (47, "Which company has the lowest average product price?"),
    (48, "Which company has the most discounted products?"),
    (49, "What percentage of this company's products are available?"),
    (50, "Compare the prices of products from two companies."),
    
    # Pack Sizes (51-60)
    (51, "What is the pack size of this medicine?"),
    (52, "Show all pack sizes available for Zestril."),
    (53, "Does this medicine have multiple pack sizes?"),
    (54, "What is the largest pack available for this medicine?"),
    (55, "What is the smallest pack available for this medicine?"),
    (56, "Show medicines with a 1x14 pack size."),
    (57, "Compare the prices of different pack sizes of this medicine."),
    (58, "Which medicines are available in multiple pack sizes?"),
    (59, "Does this product have another pack option?"),
    (60, "Show all available pack options from this company."),
    
    # Dataset Analytics (61-75)
    (61, "How many pharmaceutical products are in the dataset?"),
    (62, "What is the average medicine price?"),
    (63, "What is the average price after discount?"),
    (64, "What is the highest product price?"),
    (65, "What is the lowest product price?"),
    (66, "Show the 10 most expensive products."),
    (67, "Show the 10 cheapest products."),
    (68, "Show the top 20 products by discount."),
    (69, "How many products have discounts?"),
    (70, "What percentage of products have discounts?"),
    (71, "What is the average price by company?"),
    (72, "What is the median medicine price?"),
    (73, "How many products cost less than PKR 1,000?"),
    (74, "How many available products cost less than PKR 500?"),
    (75, "Summarize the pricing and availability data."),
    
    # More Natural Pharmacy-Owner Questions (76-90)
    (76, "What affordable medicines are currently available?"),
    (77, "Which products have the best discounts right now?"),
    (78, "What products can I get for under PKR 500?"),
    (79, "Which company's products are generally cheaper?"),
    (80, "Which company offers the largest variety of products?"),
    (81, "What are the most expensive products currently available?"),
    (82, "Which unavailable products are normally inexpensive?"),
    (83, "Which products have a large difference between original and discounted price?"),
    (84, "Are there any high-priced products with good discounts?"),
    (85, "Give me a summary of products from ICI Pakistan."),
    (86, "Which products are available without any discount?"),
    (87, "What are the cheapest available products?"),
    (88, "What are the cheapest products from a specific company?"),
    (89, "Which products have the same or similar prices?"),
    (90, "Which products offer the biggest monetary saving?"),
    
    # Decision-Support / Advanced Questions (91-100)
    (91, "Which available products have the largest discounts?"),
    (92, "Which companies have the best overall product availability?"),
    (93, "Which companies have the highest proportion of discounted products?"),
    (94, "Which products appear expensive compared with the dataset's average price?"),
    (95, "Which products are significantly cheaper after discount?"),
    (96, "Which companies have both high product availability and competitive prices?"),
    (97, "What are the main pricing patterns in this dataset?"),
    (98, "What are the main availability patterns in this dataset?"),
    (99, "Which products should I review because of unusually high or low prices?"),
    (100, "Based on price, discount, pack size, and availability, give me an overview of the pharmaceutical products in this dataset")
]

print(f"Total questions to evaluate: {len(QUESTIONS)}")

# Ground truth computation helper
def compute_ground_truth(qid: int, question: str) -> Dict[str, Any]:
    gt = {}
    if qid in [1, 5, 6, 7]: # Zestril pricing
        zestril = df_clean[df_clean['Name'].astype(str).str.lower() == 'zestril']
        prices_before = zestril['Price_before'].unique().tolist()
        prices_after = zestril['Price_After'].unique().tolist()
        discounts = zestril['Discount'].unique().tolist()
        gt = {
            "medicine": "Zestril",
            "company": "ICI Pakistan Limited",
            "original_prices": prices_before, # ['Rs 202', 'Rs 388', 'Rs 748']
            "discounted_prices": prices_after, # [182.0, 350.0, 673.0]
            "discount_pct": "10% Off",
            "summary": "Zestril has 3 price points: Original Rs 202 (Discounted 182.0), Rs 388 (Discounted 350.0), Rs 748 (Discounted 673.0) with 10% discount."
        }
    elif qid == 2:
        gt = {"medicine": "Zestril", "availability": "Available"}
    elif qid == 3:
        gt = {"medicine": "Zestril", "company": "ICI Pakistan Limited"}
    elif qid == 4 or qid == 52:
        gt = {"medicine": "Zestril", "pack_size": "1x14's"}
    elif qid == 8:
        avail = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available']
        gt = {"count": len(avail), "sample_names": avail['Name'].unique()[:10].tolist()}
    elif qid in [9, 10, 21, 22, 31, 51, 53, 54, 55, 57, 59]:
        gt = {"type": "deictic/contextual", "note": "Refers to 'this medicine' / 'this product' without prior context or refers to active product."}
    elif qid == 11 or qid == 65:
        min_p = df_clean['Price_After_clean'].min()
        cheapest = df_clean[df_clean['Price_After_clean'] == min_p][['Name', 'Company', 'Price_After']].drop_duplicates().head(5).to_dict('records')
        gt = {"min_price": min_p, "cheapest_products": cheapest}
    elif qid == 12 or qid == 64:
        max_p = df_clean['Price_After_clean'].max()
        most_exp = df_clean[df_clean['Price_After_clean'] == max_p][['Name', 'Company', 'Price_After']].drop_duplicates().head(5).to_dict('records')
        gt = {"max_price": max_p, "most_expensive": most_exp}
    elif qid == 13 or qid == 78:
        under_500 = df_clean[df_clean['Price_After_clean'] < 500]
        gt = {"count": len(under_500), "sample": under_500['Name'].unique()[:5].tolist()}
    elif qid == 14:
        b_500_1000 = df_clean[(df_clean['Price_After_clean'] >= 500) & (df_clean['Price_After_clean'] <= 1000)]
        gt = {"count": len(b_500_1000), "sample": b_500_1000['Name'].unique()[:5].tolist()}
    elif qid == 15:
        over_1000 = df_clean[df_clean['Price_After_clean'] > 1000]
        gt = {"count": len(over_1000), "sample": over_1000['Name'].unique()[:5].tolist()}
    elif qid == 16 or qid == 69:
        with_disc = df_clean[df_clean['Discount'].notna() & (df_clean['Discount'] != '')]
        gt = {"count": len(with_disc), "sample": with_disc['Name'].unique()[:5].tolist()}
    elif qid == 17:
        max_d = df_clean['Discount_pct'].max()
        gt = {"max_discount_pct": max_d, "note": "All discounts in dataset are 10% Off"}
    elif qid == 18:
        d10 = df_clean[df_clean['Discount'].astype(str).str.contains('10%', na=False)]
        gt = {"count": len(d10), "sample": d10['Name'].unique()[:5].tolist()}
    elif qid == 19 or qid == 86:
        no_d = df_clean[df_clean['Discount'].isna() | (df_clean['Discount'] == '')]
        gt = {"count": len(no_d), "sample": no_d['Name'].unique()[:5].tolist()}
    elif qid == 20:
        under_300 = df_clean[df_clean['Price_After_clean'] < 300]
        gt = {"count": len(under_300), "sample": under_300['Name'].unique()[:5].tolist()}
    elif qid == 23 or qid == 90:
        top_red = df_clean.sort_values(by='Savings', ascending=False)[['Name', 'Company', 'Price_before', 'Price_After', 'Savings']].drop_duplicates().head(10).to_dict('records')
        gt = {"top_reductions": top_red}
    elif qid == 24 or qid == 84:
        exp_disc = df_clean[(df_clean['Price_After_clean'] > 1000) & (df_clean['Discount'].notna())][['Name', 'Company', 'Price_After', 'Discount']].drop_duplicates().head(5).to_dict('records')
        gt = {"count": len(df_clean[(df_clean['Price_After_clean'] > 1000) & (df_clean['Discount'].notna())]), "sample": exp_disc}
    elif qid == 25:
        gt = {"avg_discount": "10% for all discounted items (1491 rows have 10%, 139 have None/0%)"}
    elif qid == 26:
        avail = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available']
        gt = {"count": len(avail), "percentage": f"{len(avail)/len(df_clean)*100:.1f}%"}
    elif qid == 27:
        unavail = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'sold out']
        gt = {"count_sold_out": len(unavail), "total_non_available": len(df_clean) - len(df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available'])}
    elif qid == 28:
        sold_out = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'sold out']
        gt = {"count": len(sold_out), "sample": sold_out['Name'].unique()[:5].tolist()}
    elif qid == 29 or qid == 74:
        avail_under_500 = df_clean[(df_clean['Availability'].astype(str).str.strip().str.lower() == 'available') & (df_clean['Price_After_clean'] < 500)]
        gt = {"count": len(avail_under_500), "sample": avail_under_500['Name'].unique()[:5].tolist()}
    elif qid == 30:
        avail_disc = df_clean[(df_clean['Availability'].astype(str).str.strip().str.lower() == 'available') & df_clean['Discount'].notna()]
        gt = {"count": len(avail_disc), "sample": avail_disc['Name'].unique()[:5].tolist()}
    elif qid == 32 or qid == 49 or qid == 88:
        gt = {"type": "deictic/contextual", "note": "Requires company name or refers to active company"}
    elif qid == 33 or qid == 80:
        avail_by_comp = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available']['Company'].value_counts()
        gt = {"top_company": avail_by_comp.index[0] if len(avail_by_comp) > 0 else "", "top_count": int(avail_by_comp.iloc[0]) if len(avail_by_comp) > 0 else 0}
    elif qid == 34:
        avail_500_1000 = df_clean[(df_clean['Availability'].astype(str).str.strip().str.lower() == 'available') & (df_clean['Price_After_clean'] >= 500) & (df_clean['Price_After_clean'] <= 1000)]
        gt = {"count": len(avail_500_1000), "sample": avail_500_1000['Name'].unique()[:5].tolist()}
    elif qid == 35:
        avail_cnt = len(df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available'])
        gt = {"percentage": f"{avail_cnt / len(df_clean) * 100:.2f}% ({avail_cnt} / {len(df_clean)})"}
    elif qid == 36:
        ici = df_clean[df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)]
        gt = {"company": "ICI Pakistan Limited", "count": len(ici), "unique_products": ici['Name'].unique().tolist()}
    elif qid == 37:
        ici = df_clean[df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)]
        gt = {"company": "ICI Pakistan Limited", "total_rows": len(ici), "unique_products_count": ici['Name'].nunique()}
    elif qid == 38:
        ici = df_clean[df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)]
        min_p = ici['Price_After_clean'].min()
        cheapest = ici[ici['Price_After_clean'] == min_p][['Name', 'Price_After']].drop_duplicates().to_dict('records')
        gt = {"cheapest": cheapest, "min_price": min_p}
    elif qid == 39:
        ici = df_clean[df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)]
        max_p = ici['Price_After_clean'].max()
        most_exp = ici[ici['Price_After_clean'] == max_p][['Name', 'Price_After']].drop_duplicates().to_dict('records')
        gt = {"most_expensive": most_exp, "max_price": max_p}
    elif qid == 40:
        ici_avail = df_clean[(df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)) & (df_clean['Availability'].astype(str).str.strip().str.lower() == 'available')]
        gt = {"count": len(ici_avail), "products": ici_avail['Name'].unique().tolist()}
    elif qid == 41:
        ici_disc = df_clean[(df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)) & df_clean['Discount'].notna()]
        gt = {"count": len(ici_disc), "products": ici_disc['Name'].unique().tolist()}
    elif qid == 42:
        gt = {"unique_companies_count": df_clean['Company'].nunique()}
    elif qid == 43:
        top_comp = df_clean['Company'].value_counts()
        gt = {"top_company": top_comp.index[0], "product_count": int(top_comp.iloc[0])}
    elif qid == 44:
        top10 = df_clean['Company'].value_counts().head(10).to_dict()
        gt = {"top_10_companies": top10}
    elif qid == 45 or qid == 71:
        avg_by_comp = df_clean.groupby('Company')['Price_After_clean'].mean().round(2).head(10).to_dict()
        gt = {"sample_averages": avg_by_comp}
    elif qid == 46:
        comp_means = df_clean.groupby('Company')['Price_After_clean'].mean()
        gt = {"highest_avg_company": comp_means.idxmax(), "highest_avg_price": round(float(comp_means.max()), 2)}
    elif qid == 47 or qid == 79:
        comp_means = df_clean.groupby('Company')['Price_After_clean'].mean()
        gt = {"lowest_avg_company": comp_means.idxmin(), "lowest_avg_price": round(float(comp_means.min()), 2)}
    elif qid == 48 or qid == 93:
        comp_disc = df_clean[df_clean['Discount'].notna()]['Company'].value_counts()
        gt = {"most_discounted_company": comp_disc.index[0], "count": int(comp_disc.iloc[0])}
    elif qid == 50:
        gt = {"type": "comparative", "note": "Compares two companies"}
    elif qid == 56:
        p14 = df_clean[df_clean['Pack_Size'].astype(str).str.contains('1x14', na=False) | df_clean['Availability'].astype(str).str.contains('1x14', na=False)]
        gt = {"count": len(p14), "sample": p14['Name'].unique()[:5].tolist()}
    elif qid == 58:
        multi_pack = df_clean.groupby('Name')['Pack_Size'].nunique()
        multi_names = multi_pack[multi_pack > 1].index.tolist()
        gt = {"count": len(multi_names), "sample": multi_names[:5]}
    elif qid == 60:
        gt = {"type": "deictic/contextual", "note": "Requires specific company"}
    elif qid == 61:
        gt = {"total_rows": len(df_clean), "unique_products": df_clean['Name'].nunique()}
    elif qid == 62:
        gt = {"avg_original_price": round(float(df_clean['Price_before_clean'].mean()), 2)}
    elif qid == 63:
        gt = {"avg_discounted_price": round(float(df_clean['Price_After_clean'].mean()), 2)}
    elif qid == 66 or qid == 81:
        top10_exp = df_clean.sort_values(by='Price_After_clean', ascending=False)[['Name', 'Company', 'Price_After']].drop_duplicates().head(10).to_dict('records')
        gt = {"top_10_expensive": top10_exp}
    elif qid == 67 or qid == 87:
        top10_cheap = df_clean.sort_values(by='Price_After_clean', ascending=True)[['Name', 'Company', 'Price_After']].drop_duplicates().head(10).to_dict('records')
        gt = {"top_10_cheapest": top10_cheap}
    elif qid == 68 or qid == 77 or qid == 91:
        top20_disc = df_clean[df_clean['Discount'].notna()].sort_values(by='Savings', ascending=False)[['Name', 'Company', 'Price_before', 'Price_After', 'Discount', 'Savings']].drop_duplicates().head(20).to_dict('records')
        gt = {"top_by_discount": top20_disc}
    elif qid == 70:
        disc_cnt = len(df_clean[df_clean['Discount'].notna()])
        gt = {"discount_count": disc_cnt, "percentage": f"{disc_cnt / len(df_clean) * 100:.2f}%"}
    elif qid == 72:
        gt = {"median_price_after": float(df_clean['Price_After_clean'].median()), "median_price_before": float(df_clean['Price_before_clean'].median())}
    elif qid == 73:
        u1000 = df_clean[df_clean['Price_After_clean'] < 1000]
        gt = {"count": len(u1000), "percentage": f"{len(u1000)/len(df_clean)*100:.2f}%"}
    elif qid == 75 or qid == 100:
        gt = {
            "total_products": len(df_clean),
            "unique_names": df_clean['Name'].nunique(),
            "unique_companies": df_clean['Company'].nunique(),
            "avg_price_before": round(float(df_clean['Price_before_clean'].mean()), 2),
            "avg_price_after": round(float(df_clean['Price_After_clean'].mean()), 2),
            "available_count": len(df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available']),
            "discounted_count": len(df_clean[df_clean['Discount'].notna()])
        }
    elif qid == 76:
        avail_under_500 = df_clean[(df_clean['Availability'].astype(str).str.strip().str.lower() == 'available') & (df_clean['Price_After_clean'] < 500)]
        gt = {"count": len(avail_under_500), "sample": avail_under_500['Name'].unique()[:5].tolist()}
    elif qid == 82:
        unavail_cheap = df_clean[(df_clean['Availability'].astype(str).str.strip().str.lower() == 'sold out') & (df_clean['Price_After_clean'] < 300)]
        gt = {"count": len(unavail_cheap), "sample": unavail_cheap['Name'].unique()[:5].tolist()}
    elif qid == 83 or qid == 95:
        top_diff = df_clean.sort_values(by='Savings', ascending=False)[['Name', 'Company', 'Price_before', 'Price_After', 'Savings']].drop_duplicates().head(10).to_dict('records')
        gt = {"top_savings": top_diff}
    elif qid == 85:
        ici = df_clean[df_clean['Company'].astype(str).str.contains('ICI Pakistan', case=False, na=False)]
        gt = {"company": "ICI Pakistan Limited", "total_records": len(ici), "unique_medicines": ici['Name'].nunique(), "products": ici['Name'].unique().tolist()}
    elif qid == 89:
        price_modes = df_clean['Price_After_clean'].value_counts().head(5).to_dict()
        gt = {"most_common_prices": price_modes}
    elif qid == 92:
        avail_comp = df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available']['Company'].value_counts().head(5).to_dict()
        gt = {"top_available_companies": avail_comp}
    elif qid == 94:
        avg_p = df_clean['Price_After_clean'].mean()
        above_avg = df_clean[df_clean['Price_After_clean'] > avg_p]
        gt = {"avg_price": round(avg_p, 2), "count_above_avg": len(above_avg), "sample": above_avg['Name'].unique()[:5].tolist()}
    elif qid == 96:
        gt = {"type": "analytical_overview", "note": "High availability & competitive pricing"}
    elif qid == 97:
        gt = {"type": "pricing_patterns", "note": f"Mean Price: PKR {df_clean['Price_After_clean'].mean():.2f}, Median: PKR {df_clean['Price_After_clean'].median():.2f}, 91.6% items have 10% discount"}
    elif qid == 98:
        gt = {"type": "availability_patterns", "note": f"Available: {len(df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'available'])}, Sold Out: {len(df_clean[df_clean['Availability'].astype(str).str.strip().str.lower() == 'sold out'])}"}
    elif qid == 99:
        gt = {"type": "outliers", "high": df_clean.nlargest(5, 'Price_After_clean')[['Name', 'Price_After']].to_dict('records'), "low": df_clean.nsmallest(5, 'Price_After_clean')[['Name', 'Price_After']].to_dict('records')}
    else:
        gt = {"general": "General query"}
    return gt

print("Ground truth generator loaded successfully.")
