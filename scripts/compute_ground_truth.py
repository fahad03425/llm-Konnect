import pandas as pd
import numpy as np

# Load dataset
df = pd.read_excel('data/uploads/pharmacy_compliant_master_dataset.xlsx')
print(f"Loaded {len(df)} rows.")

# Ensure numeric columns
df['Quantity_Sold'] = pd.to_numeric(df['Quantity_Sold'], errors='coerce').fillna(0)
df['Unit_Cost_Price_USD'] = pd.to_numeric(df['Unit_Cost_Price_USD'], errors='coerce').fillna(0)
df['Maximum_Retail_Price_USD'] = pd.to_numeric(df['Maximum_Retail_Price_USD'], errors='coerce').fillna(0)
df['Total_Transaction_Value_USD'] = pd.to_numeric(df['Total_Transaction_Value_USD'], errors='coerce').fillna(0)
df['Timestamp'] = pd.to_datetime(df['Timestamp'])

print("\n--- GROUND TRUTH SUMMARY ---")

# Q1: Which therapeutic class generates the most revenue?
tc_rev = df.groupby('Therapeutic_Class')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
print("Q1 (TC most rev):", tc_rev.index[0], f"(${tc_rev.iloc[0]:,.2f})")

# Q2: Which therapeutic class sells the most units?
tc_qty = df.groupby('Therapeutic_Class')['Quantity_Sold'].sum().sort_values(ascending=False)
print("Q2 (TC most units):", tc_qty.index[0], f"({tc_qty.iloc[0]:,} units)")

# Q3: What are the top five therapeutic classes by sales?
print("Q3 (Top 5 TC by sales):", tc_rev.head(5).to_dict())

# Q4: Which therapeutic classes have the lowest sales?
print("Q4 (Lowest TC by sales):", tc_rev.tail(5).to_dict())

# Q5: How many different medicines have been sold?
print("Q5 (Distinct medicines):", df['Product_Name'].nunique())

# Q6: Which manufacturer generates the most revenue?
mfg_rev = df.groupby('Manufacturer')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
print("Q6 (Mfg most rev):", mfg_rev.index[0], f"(${mfg_rev.iloc[0]:,.2f})")

# Q7: Which manufacturer's products sell the most units?
mfg_qty = df.groupby('Manufacturer')['Quantity_Sold'].sum().sort_values(ascending=False)
print("Q7 (Mfg most units):", mfg_qty.index[0], f"({mfg_qty.iloc[0]:,} units)")

# Q8: Show me the top 10 manufacturers by sales.
print("Q8 (Top 10 Mfg sales):", mfg_rev.head(10).to_dict())

# Q9: Which manufacturers have the fewest sales?
print("Q9 (Fewest sales Mfg):", mfg_rev.tail(5).to_dict())

# Q10: How many different manufacturers are represented?
print("Q10 (Distinct Mfg count):", df['Manufacturer'].nunique())

# Q11: What products from AstraZeneca have been sold?
az_prods = df[df['Manufacturer'].str.contains('AstraZeneca', case=False, na=False)]['Product_Name'].unique().tolist()
print("Q11 (AstraZeneca prods):", az_prods)

# Q12: How much revenue came from AstraZeneca products?
az_rev = df[df['Manufacturer'].str.contains('AstraZeneca', case=False, na=False)]['Total_Transaction_Value_USD'].sum()
print("Q12 (AstraZeneca rev):", f"${az_rev:,.2f}")

# Q13: Which manufacturer's products have the highest average retail price?
mfg_avg_mrp = df.groupby('Manufacturer')['Maximum_Retail_Price_USD'].mean().sort_values(ascending=False)
print("Q13 (Mfg highest avg retail price):", mfg_avg_mrp.index[0], f"(${mfg_avg_mrp.iloc[0]:.2f})")

# Q14: Which manufacturer has the largest number of products?
mfg_prod_cnt = df.groupby('Manufacturer')['Product_Name'].nunique().sort_values(ascending=False)
print("Q14 (Mfg largest # products):", mfg_prod_cnt.index[0], f"({mfg_prod_cnt.iloc[0]} products)")

# Q15: Compare sales between the major manufacturers.
print("Q15 (Major Mfg sales comparison):", mfg_rev.head(5).to_dict())

# Q16: What are my total sales by branch?
br_sales = df.groupby('Pharmacy_Branch')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False)
print("Q16 (Sales by branch):", br_sales.to_dict())

# Q17: Which pharmacy branch has the highest sales?
print("Q17 (Highest sales branch):", br_sales.index[0], f"(${br_sales.iloc[0]:,.2f})")

# Q18: Which branch has sold the most units?
br_qty = df.groupby('Pharmacy_Branch')['Quantity_Sold'].sum().sort_values(ascending=False)
print("Q18 (Branch most units):", br_qty.index[0], f"({br_qty.iloc[0]:,} units)")

# Q19: Which branch has the highest average transaction value?
br_avg_tx = df.groupby('Pharmacy_Branch')['Total_Transaction_Value_USD'].mean().sort_values(ascending=False)
print("Q19 (Highest avg tx val):", br_avg_tx.index[0], f"(${br_avg_tx.iloc[0]:.2f})")

# Q20: What are the top-selling products at each branch?
for br in df['Pharmacy_Branch'].unique():
    top_p = df[df['Pharmacy_Branch'] == br].groupby('Product_Name')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False).head(3)
    print(f"  Branch {br} top products:", top_p.to_dict())

# Q21: Which therapeutic class performs best at each branch?
for br in df['Pharmacy_Branch'].unique():
    top_tc = df[df['Pharmacy_Branch'] == br].groupby('Therapeutic_Class')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False).head(1)
    print(f"  Branch {br} best TC:", top_tc.index[0], f"(${top_tc.iloc[0]:,.2f})")

# Q22: Which manufacturer performs best at each branch?
for br in df['Pharmacy_Branch'].unique():
    top_m = df[df['Pharmacy_Branch'] == br].groupby('Manufacturer')['Total_Transaction_Value_USD'].sum().sort_values(ascending=False).head(1)
    print(f"  Branch {br} best Mfg:", top_m.index[0], f"(${top_m.iloc[0]:,.2f})")

# Q23: Compare revenue across all pharmacy branches.
print("Q23 (Compare branch revenue):", br_sales.to_dict())

# Q24: Which branch has the most transactions?
br_txn_cnt = df['Pharmacy_Branch'].value_counts()
print("Q24 (Branch most txns):", br_txn_cnt.index[0], f"({br_txn_cnt.iloc[0]} txns)")

# Q25: Which branch has the lowest sales?
print("Q25 (Branch lowest sales):", br_sales.index[-1], f"(${br_sales.iloc[-1]:,.2f})")

# Q26: What were my sales today according to the dataset?
# Latest date in dataset
latest_date = df['Timestamp'].dt.date.max()
sales_today = df[df['Timestamp'].dt.date == latest_date]['Total_Transaction_Value_USD'].sum()
print("Q26 (Sales today / latest date:", latest_date, "):", f"${sales_today:,.2f}")

# Q27: What were my sales in January?
df['YearMonth'] = df['Timestamp'].dt.to_period('M')
df['MonthName'] = df['Timestamp'].dt.month_name()
jan_sales = df[df['Timestamp'].dt.month == 1]['Total_Transaction_Value_USD'].sum()
print("Q27 (Sales in January):", f"${jan_sales:,.2f}")

# Q28: What were my total monthly sales?
monthly_sales = df.groupby('YearMonth')['Total_Transaction_Value_USD'].sum()
print("Q28 (Total monthly sales):", {str(k): round(v, 2) for k, v in monthly_sales.to_dict().items()})

# Q29: Which month had the highest sales?
print("Q29 (Highest month):", str(monthly_sales.index[monthly_sales.argmax()]), f"(${monthly_sales.max():,.2f})")

# Q30: Which month had the lowest sales?
print("Q30 (Lowest month):", str(monthly_sales.index[monthly_sales.argmin()]), f"(${monthly_sales.min():,.2f})")

# Q31: How have sales changed over time?
print("Q31 (Sales change over time summary):", {str(k): round(v, 2) for k, v in monthly_sales.to_dict().items()})

# Q32: Which day had the highest revenue?
daily_rev = df.groupby(df['Timestamp'].dt.date)['Total_Transaction_Value_USD'].sum()
print("Q32 (Day highest rev):", daily_rev.index[daily_rev.argmax()], f"(${daily_rev.max():,.2f})")

# Q33: Which day had the most transactions?
daily_txns = df.groupby(df['Timestamp'].dt.date)['Transaction_UUID'].count()
print("Q33 (Day most txns):", daily_txns.index[daily_txns.argmax()], f"({daily_txns.max()} txns)")

# Q34: What are my average daily sales?
avg_daily = daily_rev.mean()
print("Q34 (Avg daily sales):", f"${avg_daily:,.2f}")

# Q35: Show me the monthly sales trend.
print("Q35 (Monthly trend):", {str(k): round(v, 2) for k, v in monthly_sales.to_dict().items()})

# Q36: What is the cost price of Betaloc 50mg Extended-Release?
betaloc_cost = df[df['Product_Name'].str.contains('Betaloc', case=False, na=False)]['Unit_Cost_Price_USD'].unique()
print("Q36 (Betaloc cost):", betaloc_cost)

# Q37: What is the maximum retail price of Betaloc 50mg Extended-Release?
betaloc_mrp = df[df['Product_Name'].str.contains('Betaloc', case=False, na=False)]['Maximum_Retail_Price_USD'].unique()
print("Q37 (Betaloc MRP):", betaloc_mrp)

# Distinct product pricing
prod_pricing = df.groupby('Product_Name').agg({
    'Unit_Cost_Price_USD': 'mean',
    'Maximum_Retail_Price_USD': 'mean'
}).reset_index()
prod_pricing['Price_Diff'] = prod_pricing['Maximum_Retail_Price_USD'] - prod_pricing['Unit_Cost_Price_USD']
prod_pricing['Gross_Margin_Pct'] = (prod_pricing['Price_Diff'] / prod_pricing['Maximum_Retail_Price_USD']) * 100

# Q38: Which products have the highest maximum retail prices?
highest_mrp = prod_pricing.sort_values('Maximum_Retail_Price_USD', ascending=False).head(5)
print("Q38 (Highest MRP):", highest_mrp[['Product_Name', 'Maximum_Retail_Price_USD']].to_dict('records'))

# Q39: Which products have the lowest maximum retail prices?
lowest_mrp = prod_pricing.sort_values('Maximum_Retail_Price_USD', ascending=True).head(5)
print("Q39 (Lowest MRP):", lowest_mrp[['Product_Name', 'Maximum_Retail_Price_USD']].to_dict('records'))

# Q40: Which products have the biggest difference between cost price and maximum retail price?
biggest_diff = prod_pricing.sort_values('Price_Diff', ascending=False).head(5)
print("Q40 (Biggest price diff):", biggest_diff[['Product_Name', 'Price_Diff', 'Unit_Cost_Price_USD', 'Maximum_Retail_Price_USD']].to_dict('records'))

# Q41: Which products have the smallest margin between cost and maximum retail price?
smallest_diff = prod_pricing.sort_values('Price_Diff', ascending=True).head(5)
print("Q41 (Smallest price diff):", smallest_diff[['Product_Name', 'Price_Diff', 'Unit_Cost_Price_USD', 'Maximum_Retail_Price_USD']].to_dict('records'))

# Q42: Which medicines potentially provide the highest gross margin per unit?
highest_margin_unit = prod_pricing.sort_values('Price_Diff', ascending=False).head(5)
print("Q42 (Highest unit margin / $ diff):", highest_margin_unit[['Product_Name', 'Price_Diff']].to_dict('records'))

# Q43: What is the estimated gross margin for each product based on the recorded prices?
print("Q43 (Estimated gross margin count):", len(prod_pricing), "distinct products computed.")
