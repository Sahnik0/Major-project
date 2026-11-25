# User Guide: Sales Forecasting & Stock Advisor

Welcome to the **Demand Forecasting & Inventory Advisor**! This guide explains how to upload your sales transactions, review data health, understand your demand patterns, and use the inventory recommendations.

---

## 1. Preparing Your Sales File
You can upload your sales history as a standard Excel file (`.xlsx`) or spreadsheet (`.csv`).

### Minimum Required Information:
- **Date**: The day the sale happened (e.g. `2023-10-15` or `15/10/2023`).
- **Product**: Product name, SKU, or barcode (e.g. `Blue Denim Jeans M` or `SKU-1029`).
- **Quantity**: How many units were sold (e.g. `3`).

### Helpful Optional Information:
- **Revenue or Price**: Total sales amount for the transaction.
- **Store or Branch**: If you operate multiple store locations.
- **Promotion Flag**: Mark `1` if a special discount or marketing promotion was active.
- **Stock on Hand**: How many units you currently have in your warehouse or backroom.

---

## 2. Uploading & Confirming Columns
1. Click **Upload sales file** on the Home screen or drag and drop your file.
2. The system automatically reads the file and suggests which column is which.
3. Review the preview on screen. If a column was mismatched, select the correct column from the dropdown.
4. Click **Looks right, continue** to process your data.

---

## 3. Understanding the Data Health Report
Before forecasting, the system reviews your data for errors or patterns:
- **Duplicates**: Multiple identical records are identified.
- **Returns**: Negative quantities are automatically separated as returns.
- **Spikes & Outliers**: Unusual sales surges (like flash sales or data entry errors) are flagged.
- **Demand Types**:
  - **Smooth**: Products that sell steadily day in, day out (easy to forecast).
  - **Erratic**: Products sold frequently, but in wildly unpredictable order quantities.
  - **Intermittent**: Products that sell occasionally with several quiet days in between.
  - **Lumpy**: Infrequent sales with large, uneven batch sizes.
  - **New / Short History**: Products recently introduced (< 60 days of sales).
  - **Inactive**: Products with no recorded sales in the last 60 days (possibly discontinued).

---

## 4. Privacy Guarantee
All calculations, database storage, and AI summaries happen **locally on your machine**. Your customer records, pricing, and sales figures are never uploaded to any external cloud service.

