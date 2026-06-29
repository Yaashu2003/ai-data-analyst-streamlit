import zipfile, os, io, shutil, csv, xml.etree.ElementTree as ET
from textwrap import dedent

DATA_DIR = '.'
OUT_DIR  = '.'
os.makedirs(OUT_DIR, exist_ok=True)

def read_csv_headers(fname):
    with open(f'{DATA_DIR}/{fname}', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return list(rows[0].keys()), rows

def tableau_datatype(col, sample_vals):
    """Infer Tableau datatype from column name and values"""
    col_low = col.lower()
    if any(k in col_low for k in ['date','month','year','period']):
        return 'date'
    if any(k in col_low for k in ['id','_id','code','flag','status','type','grade','stage','channel','segment','branch','gender','city','agent','product']):
        return 'string'
    if any(k in col_low for k in ['amount','balance','rate','pct','score','count','ratio','cr','dpd','tenure','days','txns']):
        return 'real'
    # Try parsing
    for v in sample_vals[:5]:
        if v:
            try:
                float(v)
                return 'real'
            except:
                pass
    return 'string'

def make_datasource_xml(datasource_name, csv_filename, cols_schema):
    """Build Tableau datasource XML section"""
    cols_xml = ''
    for col, dtype in cols_schema:
        safe = col.replace(' ', '_').replace('%', 'pct').replace('(', '').replace(')', '')
        role = 'measure' if dtype in ('real','integer') else 'dimension'
        agg = 'Sum' if dtype in ('real','integer') else 'Count'
        cols_xml += f'''
    <column datatype="{dtype}" name="[{safe}]" role="{role}" type="{'quantitative' if role=='measure' else 'nominal'}" caption="{col}">
      <calculation formula="[{safe}]"/>
    </column>'''
    
    return f'''
  <datasource name="{datasource_name}" inline="true" caption="{datasource_name}">
    <connection class="text" filename="{csv_filename}" name="{datasource_name.replace(' ','_')}" >
      <relation name="{csv_filename}" table="{csv_filename}" type="text" />
    </connection>
    <column-instance column="[Number of Records]" derivation="Count" name="[cnt:Number of Records:qk]" pivot="key" type="quantitative"/>
    {cols_xml}
  </datasource>'''

def make_worksheet_xml(ws_name, title, chart_type_hint, datasource_name, cols_schema, viz_desc):
    """Build a Tableau worksheet XML"""
    # Pick columns for rows/cols shelves based on chart hints
    dims = [(c,d) for c,d in cols_schema if d == 'string']
    measures = [(c,d) for c,d in cols_schema if d in ('real','integer')]
    
    row_col = dims[0][0].replace(' ','_') if dims else 'branch'
    col_col = measures[0][0].replace(' ','_') if measures else 'sanction_amount'
    color_col = dims[1][0].replace(' ','_') if len(dims)>1 else row_col
    
    mark_type = {
        'bar': 'Bar', 'line': 'Line', 'pie': 'Pie', 
        'scatter': 'Circle', 'heatmap': 'Square', 'area': 'Area'
    }.get(chart_type_hint, 'Bar')
    
    return f'''
  <worksheet name="{ws_name}">
    <table>
      <view>
        <datasources>
          <datasource caption="{datasource_name}" name="{datasource_name.replace(' ','_')}"/>
        </datasources>
        <datasource-dependencies datasource="{datasource_name.replace(' ','_')}">
          <column datatype="string" name="[{row_col}]" role="dimension" type="nominal" caption="{row_col}"/>
          <column datatype="real" name="[{col_col}]" role="measure" type="quantitative" caption="{col_col}"/>
          <column datatype="string" name="[{color_col}]" role="dimension" type="nominal" caption="{color_col}"/>
        </datasource-dependencies>
        <rows>[{row_col}]</rows>
        <cols>SUM([{col_col}])</cols>
        <filter class="categorical" column="[{row_col}]">
          <groupfilter function="level-members" level="[{row_col}]"/>
        </filter>
        <marks>
          <mark type="{mark_type}"/>
        </marks>
        <encodings>
          <color column="[{color_col}]"/>
          <size column="SUM([{col_col}])"/>
          <text column="SUM([{col_col}])"/>
        </encodings>
      </view>
      <style>
        <style-rule element="mark">
          <encoding attr="color" field="[{color_col}]" type="palette"/>
        </style-rule>
      </style>
    </table>
    <!-- viz_description: {viz_desc} -->
  </worksheet>'''

def make_dashboard_xml(db_name, worksheet_names, layout_desc):
    """Build a Tableau dashboard XML"""
    zones = ''
    cols_n = 2
    for i, ws in enumerate(worksheet_names):
        x_pct = (i % cols_n) * 50
        y_pct = (i // cols_n) * 50
        zones += f'''
      <zone h="50%" name="az_{i}" type="layout-basic" w="50%" x="{x_pct}%" y="{y_pct}%">
        <zone h="100%" name="az_{i}_ws" type="worksheet" w="100%" x="0%" y="0%">
          <worksheet name="{ws}"/>
        </zone>
      </zone>'''
    
    return f'''
  <dashboard name="{db_name}" type="automatic">
    <style/>
    <size maxheight="1200" maxwidth="1800" minheight="600" minwidth="800"/>
    <zones>
      <zone h="100%" id="1" type="layout-flow" w="100%" x="0%" y="0%">
        {zones}
      </zone>
    </zones>
    <layout>
      <zone h="100%" id="1" type="layout-flow" w="100%" x="0%" y="0%"/>
    </layout>
    <devicelayouts/>
  </dashboard>'''

def build_twb(workbook_name, pages, datasource_name, csv_filename, cols_schema):
    """Build complete .twb XML string"""
    ds_xml = make_datasource_xml(datasource_name, csv_filename, cols_schema)
    
    worksheets_xml = ''
    all_ws_names = []
    for pg in pages:
        for chart in pg['charts']:
            ws_name = chart['ws_name']
            all_ws_names.append(ws_name)
            worksheets_xml += make_worksheet_xml(
                ws_name, chart['title'], chart.get('chart_type','bar'),
                datasource_name, cols_schema, chart['description']
            )
    
    dashboards_xml = ''
    for pg in pages:
        ws_names = [c['ws_name'] for c in pg['charts']]
        dashboards_xml += make_dashboard_xml(pg['name'], ws_names, pg.get('desc',''))
    
    twb = f'''<?xml version='1.0' encoding='utf-8' ?>
<workbook source-build='2024.1.0' source-platform='win' version='21.4' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest/>
  <preferences>
    <color-palette name="BFSI Blue" type="regular">
      <color>#003B71</color><color>#0072CE</color><color>#00A3E0</color>
      <color>#00B140</color><color>#FF6B35</color><color>#FFD100</color>
    </color-palette>
  </preferences>
  <datasources>
    {ds_xml}
  </datasources>
  <worksheets>
    {worksheets_xml}
  </worksheets>
  <dashboards>
    {dashboards_xml}
  </dashboards>
  <windows>
    <window class="dashboard" maximized="true" name="{pages[0]['name']}">
      <viewpoint/>
      <active id="-1"/>
    </window>
  </windows>
  <!-- Workbook: {workbook_name} | Built for BFSI Cross-Analytics Agent Testing -->
</workbook>'''
    return twb

def make_twbx(out_path, workbook_name, pages, datasource_name, csv_filename, cols_schema, rows):
    twb_xml = build_twb(workbook_name, pages, datasource_name, csv_filename, cols_schema)
    twb_name = workbook_name.replace(' ','_') + '.twb'
    
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Main workbook XML
        zf.writestr(twb_name, twb_xml)
        
        # Embedded data
        csv_buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(csv_buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        zf.writestr(f'Data/{csv_filename}', csv_buf.getvalue())
    
    sz = os.path.getsize(out_path) // 1024
    print(f"  [OK] {os.path.basename(out_path)}: {len(rows)} rows embedded -> {sz}KB")

def schema(fname):
    cols, rows = read_csv_headers(fname)
    result = []
    for col in cols:
        samples = [r[col] for r in rows[:10] if r.get(col)]
        result.append((col, tableau_datatype(col, samples)))
    return result, rows

branch_schema, branch_rows = schema('branch_performance.csv')
cust_schema, cust_rows = schema('customer_transactions.csv')
coll_schema, coll_rows = schema('collections_recovery.csv')

make_twbx(
    out_path=f'{OUT_DIR}/TWBX1_BranchPerformance.twbx',
    workbook_name='Branch Performance Dashboard',
    datasource_name='Branch Performance',
    csv_filename='branch_performance.csv',
    cols_schema=branch_schema,
    rows=branch_rows,
    pages=[
        {
            'name': 'Branch KPI Overview',
            'desc': 'Branch achievement vs target, deposits, digital adoption',
            'charts': [
                {'ws_name': 'achievement_vs_target', 'title': 'Loan Achievement % vs Target by Branch', 'chart_type': 'bar', 'description': 'Bar: branch X, achievement_pct Y, reference line at 100%'},
                {'ws_name': 'deposit_trend', 'title': 'Net Deposit Trend by Branch (Monthly)', 'chart_type': 'line', 'description': 'Line: month X, net_deposit_cr Y, line per branch'},
                {'ws_name': 'digital_adoption', 'title': 'Digital Adoption % vs ATM Usage', 'chart_type': 'scatter', 'description': 'Scatter: digital_adoption_pct X, atm_transactions Y, branch color'},
                {'ws_name': 'new_customers', 'title': 'New Customer Acquisition by Branch', 'chart_type': 'area', 'description': 'Area: month X, new_customers Y, stacked by branch'},
            ]
        },
        {
            'name': 'Operations & Service Quality',
            'desc': 'Turnaround times, complaints, NPA by branch',
            'charts': [
                {'ws_name': 'complaints_resolved', 'title': 'Complaints Received vs Resolved', 'chart_type': 'bar', 'description': 'Side-by-side bar: complaints_received vs complaints_resolved per branch'},
                {'ws_name': 'turnaround', 'title': 'Avg Loan Turnaround Days by Branch', 'chart_type': 'bar', 'description': 'Horizontal bar: avg_turnaround_days, sorted asc (lower=better)'},
                {'ws_name': 'npa_heatmap', 'title': 'NPA Amount by Branch × Month Heatmap', 'chart_type': 'heatmap', 'description': 'Heatmap: branch rows, month cols, npa_amount_cr color'},
                {'ws_name': 'staff_efficiency', 'title': 'Loans per Staff Member by Branch', 'chart_type': 'bar', 'description': 'Calc: loans_disbursed_count/staff_count ratio per branch'},
            ]
        }
    ]
)

make_twbx(
    out_path=f'{OUT_DIR}/TWBX2_CustomerAnalytics.twbx',
    workbook_name='Customer Transaction Analytics',
    datasource_name='Customer Transactions',
    csv_filename='customer_transactions.csv',
    cols_schema=cust_schema,
    rows=cust_rows[:2000],
    pages=[
        {
            'name': 'Transaction Behaviour',
            'charts': [
                {'ws_name': 'txn_category_split', 'title': 'Transaction Volume by Category', 'chart_type': 'pie', 'description': 'Pie/treemap: txn_category, count of transactions'},
                {'ws_name': 'channel_usage', 'title': 'Channel-wise Transaction Count (Monthly)', 'chart_type': 'line', 'description': 'Line: txn_date monthly, count, color per channel'},
                {'ws_name': 'debit_credit_bar', 'title': 'Debit vs Credit Amount by Month', 'chart_type': 'bar', 'description': 'Grouped bar: month X, sum(debit_amount) and sum(credit_amount) side by side'},
                {'ws_name': 'city_txn_map', 'title': 'Transaction Volume by City', 'chart_type': 'bar', 'description': 'Bar or symbol map: city, total transaction value'},
            ]
        },
        {
            'name': 'Customer Spend Patterns',
            'charts': [
                {'ws_name': 'top_customers', 'title': 'Top 20 Customers by Total Debit', 'chart_type': 'bar', 'description': 'Horizontal bar: customer_id, sum(debit_amount), top 20'},
                {'ws_name': 'channel_amount', 'title': 'Avg Transaction Amount by Channel', 'chart_type': 'bar', 'description': 'Bar: channel X, avg transaction size (debit+credit) Y'},
                {'ws_name': 'seasonal_trend', 'title': 'Monthly Transaction Seasonality', 'chart_type': 'area', 'description': 'Area: month X, count+amount Y - identify seasonal spikes'},
                {'ws_name': 'category_by_city', 'title': 'Transaction Category Mix by City', 'chart_type': 'heatmap', 'description': 'Heatmap: city rows, txn_category cols, transaction count'},
            ]
        }
    ]
)

make_twbx(
    out_path=f'{OUT_DIR}/TWBX3_Collections_Recovery.twbx',
    workbook_name='Collections and Recovery Dashboard',
    datasource_name='Collections Recovery',
    csv_filename='collections_recovery.csv',
    cols_schema=coll_schema,
    rows=coll_rows,
    pages=[
        {
            'name': 'Recovery Performance',
            'desc': 'Recovery rates, amounts, agent performance',
            'charts': [
                {'ws_name': 'recovery_by_branch', 'title': 'Recovery Rate % by Branch', 'chart_type': 'bar', 'description': 'Bar: branch X, avg recovery_rate_pct Y'},
                {'ws_name': 'recovery_by_stage', 'title': 'Amount Recovered by Collection Stage', 'chart_type': 'bar', 'description': 'Bar: collection_stage X, sum(amount_recovered) Y'},
                {'ws_name': 'dpd_vs_recovery', 'title': 'DPD vs Recovery Rate - Scatter', 'chart_type': 'scatter', 'description': 'Scatter: dpd X, recovery_rate_pct Y, risk_grade color, product_type shape'},
                {'ws_name': 'agent_performance', 'title': 'Agent Recovery Performance (Top 20)', 'chart_type': 'bar', 'description': 'Bar: assigned_agent, sum(amount_recovered), top 20 agents'},
            ]
        },
        {
            'name': 'Delinquency Analysis',
            'desc': 'NPA linkage, product-level delinquency',
            'charts': [
                {'ws_name': 'product_delinquency', 'title': 'Delinquent Loans by Product Type', 'chart_type': 'pie', 'description': 'Pie: product_type proportions among delinquent loans'},
                {'ws_name': 'collateral_recovery', 'title': 'Collateral Value vs Amount Recovered', 'chart_type': 'scatter', 'description': 'Scatter: collateral_value X, amount_recovered Y'},
                {'ws_name': 'credit_score_recovery', 'title': 'Credit Score Bands vs Recovery Rate', 'chart_type': 'bar', 'description': 'Bar: credit_score banded (580-640,641-700,701-760,761+), avg recovery_rate_pct'},
                {'ws_name': 'contact_effectiveness', 'title': 'Contact Attempts vs Recovery Outcome', 'chart_type': 'scatter', 'description': 'Scatter: contact_attempts X, amount_recovered Y'},
            ]
        }
    ]
)

print('Done building TWBX files.')
