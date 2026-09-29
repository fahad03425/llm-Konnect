import urllib.request
import json
import time
import pandas as pd

df = pd.read_excel('C:/Users/User/Downloads/llm-Konnect (3)/llm-konnect/data/uploads/pharmacy_single_store_data.xlsx')

sales = df[df['TransactionType'] == 'Sale'].copy()
purchases = df[df['TransactionType'] == 'Purchase'].copy()

print("==================================================")
print("COMPLEX ANALYTICS BEYOND STANDARD POS CAPABILITY")
print("==================================================")

# 1. Cashier Anomaly & Return Risk
cashier_perf = sales.groupby('Cashier')['Status'].value_counts().unstack().fillna(0)
cashier_perf['Total'] = cashier_perf['Completed'] + cashier_perf['Returned']
cashier_perf['Return_Rate_%'] = (cashier_perf['Returned'] / cashier_perf['Total']) * 100
print("\n[1] Cashier Return Anomaly Analysis:")
print(cashier_perf[['Completed', 'Returned', 'Total', 'Return_Rate_%']])

# 2. Supplier Concentration Risk (Herfindahl-Hirschman / Pareto)
sup_spend = purchases.groupby('Supplier')['Amount'].sum().sort_values(ascending=False)
total_spend = sup_spend.sum()
top3_spend = sup_spend.head(3).sum()
print(f"\n[2] Supplier Procurement Concentration Risk:")
print(f"Total Procurement Spend: PKR {total_spend:,.2f}")
print(f"Top 3 Suppliers Spend: PKR {top3_spend:,.2f} ({top3_spend/total_spend*100:.2f}% of entire budget)")
print(sup_spend.head(5))

# 3. Supply Chain Bottleneck: Locked Capital in Pending/Partial POs
pending_po = purchases[purchases['Status'].isin(['Pending', 'Partially Received'])]
locked_capital = pending_po['Amount'].sum()
print(f"\n[3] Supply Chain Liquidity Risk (Pending/Partial POs):")
print(f"Total Capital Locked: PKR {locked_capital:,.2f} across {len(pending_po)} purchase orders")
print("Breakdown by Status:\n", pending_po.groupby('Status')['Amount'].agg(['count', 'sum']))

# 4. Return Vulnerability by Category
cat_perf = sales.groupby('Category')['Status'].value_counts().unstack().fillna(0)
cat_perf['Total'] = cat_perf['Completed'] + cat_perf['Returned']
cat_perf['Return_Rate_%'] = (cat_perf['Returned'] / cat_perf['Total']) * 100
print("\n[4] Top 5 Product Categories with Highest Return Rate:")
print(cat_perf.sort_values('Return_Rate_%', ascending=False)[['Completed', 'Returned', 'Return_Rate_%']].head(5))

# 5. Discount Impact on Gross Revenue & Return Likelihood
discounted_sales = sales[sales['DiscountPct'] > 0]
full_price_sales = sales[sales['DiscountPct'] == 0]
print(f"\n[5] Discount Efficacy & Return Correlation:")
print(f"Discounted Sales Return Rate: {(discounted_sales['Status']=='Returned').mean()*100:.2f}%")
print(f"Full-Price Sales Return Rate: {(full_price_sales['Status']=='Returned').mean()*100:.2f}%")

# 6. Peak-Hour Payment Method Shifts (Evening 17:00-22:00 vs Morning 08:00-13:00)
sales['Hour'] = pd.to_datetime(sales['Time'].astype(str), format='%H:%M:%S', errors='coerce').dt.hour
evening_sales = sales[sales['Hour'].between(17, 22)]
morning_sales = sales[sales['Hour'].between(8, 13)]
print(f"\n[6] Digital Payment Adoption: Evening Peak vs Morning:")
print("Evening Payment Share (%):\n", (evening_sales['PaymentMethod'].value_counts(normalize=True)*100).round(2))
print("Morning Payment Share (%):\n", (morning_sales['PaymentMethod'].value_counts(normalize=True)*100).round(2))

# 7. Procurement vs Sales Realization Ratio
prod_sales = sales.groupby('ProductName')['Amount'].sum()
prod_purchases = purchases.groupby('ProductName')['Amount'].sum()
prod_comparison = pd.DataFrame({'Sales_Amount': prod_sales, 'Purchase_Amount': prod_purchases}).fillna(0)
prod_comparison['Deficit_or_Surplus'] = prod_comparison['Sales_Amount'] - prod_comparison['Purchase_Amount']
print("\n[7] High Purchase Spend vs Low Sales Realization (Top 5 Deficits):")
print(prod_comparison.sort_values('Deficit_or_Surplus').head(5))
