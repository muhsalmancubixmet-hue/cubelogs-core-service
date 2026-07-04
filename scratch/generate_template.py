import pandas as pd
import os

public_dir = r"c:\Users\user\Documents\project\fadi\1\cubelogs\frontend-app\public"
template_xlsx_path = os.path.join(public_dir, "Download_Employee_Template.xlsx")
template_csv_path = os.path.join(public_dir, "Download_Employee_Template.csv")

df = pd.DataFrame(columns=['Full Name', 'Email Address', 'Phone Number', 'Designation Role(s)'])
df.loc[0] = ['Rahul Das', 'rahul.das@cubelogs.com', '919877000000', 'md']
df.loc[1] = ['Sneha Nair', 'sneha.nair@cubelogs.com', '919877000001', 'developer']

# Ensure public dir exists
os.makedirs(public_dir, exist_ok=True)

df.to_excel(template_xlsx_path, index=False)
df.to_csv(template_csv_path, index=False)
print("Excel template generated successfully at:", template_xlsx_path)
print("CSV template generated successfully at:", template_csv_path)

