import pandas as pd
import numpy as np
import json
import re

file_path = 'C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/data/uploads/Pakistan_Pharmaceutical_Products_Pricing_and_Availability_Data.xlsx'
df = pd.read_excel(file_path)

print("Columns:", df.columns.tolist())
print("Total rows:", len(df))

# Clean df
df_clean = df.dropna(how='all').copy()
print("Valid rows:", len(df_clean))

# Parse Price_before
def clean_num(val):
    if pd.isna(val):
        return np.nan
    s = str(val).replace('PKR', '').replace('Rs', '').replace(',', '').replace('%', '').strip()
    try:
        return float(s)
    except:
        return np.nan

df_clean['Price_before_num'] = df_clean['Price_before'].apply(clean_num)
df_clean['Price_After_num'] = df_clean['Price_After'].apply(clean_num)
df_clean['Discount_num'] = df_clean['Discount'].apply(clean_num)
df_clean['Discount_amount'] = df_clean['Price_before_num'] - df_clean['Price_After_num']

print("\n--- Summary Stats ---")
print("Price_before_num summary:")
print(df_clean['Price_before_num'].describe())
print("\nPrice_After_num summary:")
print(df_clean['Price_After_num'].describe())
print("\nDiscount_num summary:")
print(df_clean['Discount_num'].describe())
print("\nAvailability values:")
print(df_clean['Availability'].value_counts(dropna=False))

print("\nTop 5 expensive:")
print(df_clean.sort_values(by='Price_After_num', ascending=False)[['Name', 'Company', 'Price_before', 'Discount', 'Price_After', 'Availability']].head(5))

print("\nTop 5 cheapest:")
print(df_clean.sort_values(by='Price_After_num', ascending=True)[['Name', 'Company', 'Price_before', 'Discount', 'Price_After', 'Availability']].head(5))

print("\nZestril entries:")
print(df_clean[df_clean['Name'].astype(str).str.contains('Zestril', case=False)][['Name', 'Company', 'Price_before', 'Discount', 'Price_After', 'Pack_Size', 'Availability']])
