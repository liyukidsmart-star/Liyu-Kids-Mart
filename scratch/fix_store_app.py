import os

store_app_html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Store Management - Liyu Kids Mart</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --g: #3d8b5f; --gp: #2e6b48; --p: #2e6b48; --bg: #f0f4fc;
            --sf: #FFFFFF; --tm: #12172e; --ts: #718096; --br: #e4eaf5;
            --danger: #ef4444; --warning: #f59e0b; --info: #3b82f6;
            --shadow: 0 4px 20px rgba(0,0,0,0.07);
            --shadow-md: 0 8px 32px rgba(0,0,0,0.1);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif; -webkit-tap-highlight-color: transparent; }
        body { background: var(--bg); color: var(--tm); padding-bottom: 90px; -webkit-font-smoothing: antialiased; }
        
        /* Header */
        .header { background: var(--sf); padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 16px rgba(0,0,0,0.05); backdrop-filter: blur(12px); background: rgba(255,255,255,0.85); }
        .header h1 { font-size: 1.25rem; font-weight: 800; color: var(--p); display: flex; align-items: center; gap: 8px; letter-spacing: -0.02em; }
        .header-profile { width: 34px; height: 34px; border-radius: 50%; background: linear-gradient(135deg, var(--g), var(--gp)); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; box-shadow: 0 2px 8px rgba(61,139,95,0.25); }

        /* Tabs Navigation */
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--sf); display: flex; justify-content: space-around; padding: 10px 4px calc(10px + env(safe-area-inset-bottom)); box-shadow: 0 -4px 24px rgba(0,0,0,0.08); z-index: 100; border-radius: 20px 20px 0 0; }
        .nav-item { display: flex; flex-direction: column; align-items: center; gap: 4px; color: var(--ts); font-size: 0.68rem; font-weight: 700; cursor: pointer; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); position: relative; flex: 1; text-align: center; }
        .nav-item i { font-size: 1.15rem; transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
        .nav-item.active { color: var(--p); }
        .nav-item.active i { transform: translateY(-2px) scale(1.15); color: var(--g); }
        .badge { position: absolute; top: -5px; right: 4px; background: var(--danger); color: white; font-size: 0.62rem; padding: 2px 6px; border-radius: 100px; border: 2px solid var(--sf); font-weight: 800; box-shadow: 0 2px 4px rgba(239,68,68,0.3); }

        /* Views */
        .view { display: none; padding: 18px 16px; animation: fadeIn 0.25s cubic-bezier(0.4, 0, 0.2, 1); }
        .view.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

        /* Dashboard Cards */
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 24px; }
        .stat-card { background: var(--sf); padding: 16px; border-radius: 18px; border: 1px solid var(--br); box-shadow: var(--shadow); transition: transform 0.2s; position: relative; overflow: hidden; }
        .stat-card:active { transform: scale(0.98); }
        .stat-card .title { font-size: 0.74rem; color: var(--ts); font-weight: 700; margin-bottom: 6px; display: flex; justify-content: space-between; text-transform: uppercase; letter-spacing: 0.04em; }
        .stat-card .value { font-size: 1.5rem; font-weight: 800; color: var(--tm); letter-spacing: -0.02em; }
        .stat-card.primary { background: linear-gradient(135deg, var(--g), var(--gp)); color: white; border: none; box-shadow: 0 6px 20px rgba(61,139,95,0.3); }
        .stat-card.primary .title, .stat-card.primary .value { color: white; opacity: 0.95; }

        /* Order List */
        .filter-scroll { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 12px; margin-bottom: 12px; scrollbar-width: none; }
        .filter-scroll::-webkit-scrollbar { display: none; }
        .filter-pill { padding: 7px 14px; background: var(--sf); border: 1.5px solid var(--br); border-radius: 100px; font-size: 0.8rem; font-weight: 700; color: var(--ts); white-space: nowrap; cursor: pointer; transition: all 0.2s; box-shadow: 0 2px 6px rgba(0,0,0,0.03); }
        .filter-pill.active { background: linear-gradient(135deg, var(--g), var(--gp)); color: white; border-color: transparent; box-shadow: 0 4px 12px rgba(61,139,95,0.3); }

        .order-card { background: var(--sf); border-radius: 18px; padding: 16px; margin-bottom: 14px; border: 1px solid var(--br); display: flex; flex-direction: column; gap: 12px; box-shadow: var(--shadow); position: relative; cursor: pointer; transition: transform 0.2s; }
        .order-card:active { transform: scale(0.98); }
        .order-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--br); padding-bottom: 12px; }
        .order-id { font-weight: 800; font-size: 1rem; display: flex; align-items: center; gap: 6px; color: var(--tm); }
        .order-status { font-size: 0.72rem; font-weight: 800; padding: 4px 10px; border-radius: 100px; text-transform: uppercase; letter-spacing: 0.04em; }
        .status-pending { background: #FEF3C7; color: #D97706; border: 1px solid #fde68a; }
        .status-confirmed { background: #DBEAFE; color: #1e40af; border: 1px solid #bfdbfe; }
        .status-packed { background: #F3E8FF; color: #6d28d9; border: 1px solid #e9d5ff; }
        .status-out_for_delivery { background: #ffedd5; color: #9a3412; border: 1px solid #fed7aa; }
        .status-delivered { background: #D1FAE5; color: #059669; border: 1px solid #a7f3d0; }
        .status-cancelled { background: #FEE2E2; color: #DC2626; border: 1px solid #fecaca; }
        
        .order-customer { display: flex; align-items: center; gap: 10px; }
        .customer-avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--bg); display: flex; align-items: center; justify-content: center; font-weight: 800; color: var(--p); border: 1.5px solid var(--br); }
        .customer-info .name { font-weight: 800; font-size: 0.9rem; }
        .customer-info .phone { font-size: 0.78rem; color: var(--ts); font-weight: 500; }
        
        .order-footer { display: flex; justify-content: space-between; align-items: center; background: var(--bg); padding: 10px 14px; border-radius: 12px; margin-top: 2px; }
        .order-total { font-weight: 800; font-size: 1rem; color: var(--p); }
        .order-items { font-size: 0.8rem; font-weight: 700; color: var(--ts); }
        
        .btn { padding: 9px 18px; border-radius: 100px; font-weight: 800; font-size: 0.85rem; border: none; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
        .btn:active { transform: scale(0.96); }
        .btn-primary { background: linear-gradient(135deg, var(--g), var(--gp)); color: white; box-shadow: 0 4px 12px rgba(61,139,95,0.3); }
        .btn-outline { background: var(--sf); border: 1.5px solid var(--br); color: var(--tm); }
        
        /* Inventory */
        .product-item { display: flex; align-items: center; gap: 12px; background: var(--sf); padding: 12px; border-radius: 16px; border: 1px solid var(--br); margin-bottom: 10px; box-shadow: var(--shadow); }
        .product-img { width: 50px; height: 50px; border-radius: 10px; object-fit: cover; background: var(--bg); border: 1px solid var(--br); }
        .product-info { flex: 1; }
        .product-name { font-weight: 700; font-size: 0.88rem; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.25; }
        .product-stock { display: flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 700; }
        .stock-badge { padding: 2px 7px; border-radius: 6px; background: var(--bg); font-size: 0.72rem; }
        .stock-badge.low { background: #FEE2E2; color: #DC2626; border: 1px solid #fecaca; }
        .stock-badge.good { background: #D1FAE5; color: #059669; border: 1px solid #a7f3d0; }
        .stock-actions { display: flex; align-items: center; gap: 8px; }
        .stock-btn { width: 32px; height: 32px; border-radius: 8px; border: 1.5px solid var(--br); background: var(--sf); display: flex; align-items: center; justify-content: center; font-weight: 800; color: var(--tm); font-size: 1.05rem; cursor: pointer; }

        /* Modal Overlay */
        .modal-overlay { position: fixed; inset: 0; background: rgba(18,23,46,0.6); z-index: 1000; display: none; align-items: flex-end; backdrop-filter: blur(8px); }
        .modal-content { background: var(--bg); width: 100%; border-radius: 24px 24px 0 0; padding: 20px 18px 36px; max-height: 88vh; overflow-y: auto; transform: translateY(100%); transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); box-shadow: 0 -10px 40px rgba(0,0,0,0.1); }
        .modal-overlay.active { display: flex; }
        .modal-overlay.active .modal-content { transform: translateY(0); }
        .modal-handle { width: 44px; height: 4px; background: #cbd5e1; border-radius: 100px; margin: 0 auto 20px; }
        
        .detail-row { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px dashed #cbd5e1; font-size: 0.88rem; }
        .detail-row span:last-child { font-weight: 800; }
        
        /* Driver Assign List */
        .driver-item { display: flex; align-items: center; justify-content: space-between; padding: 12px; border: 1px solid var(--br); border-radius: 14px; margin-bottom: 10px; background: var(--sf); box-shadow: var(--shadow); cursor: pointer; }
        .driver-info { display: flex; align-items: center; gap: 10px; }
        .driver-avatar { width: 40px; height: 40px; border-radius: 50%; background: #E0E7FF; color: #4338CA; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; border: 2px solid #c7d2fe; }

        /* Map Container */
        #storeMap { height: 320px; border-radius: 18px; border: 1px solid var(--br); box-shadow: var(--shadow); margin-bottom: 14px; z-index: 1; overflow: hidden; }

        .report-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 14px 0 24px; }
        .report-card { background: var(--sf); padding: 16px; border-radius: 18px; border: 1px solid var(--br); box-shadow: var(--shadow); position: relative; overflow: hidden; }
        .report-card.week::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: linear-gradient(90deg, #3d8b5f, #2e6b48); }
        .report-card.biweek::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: linear-gradient(90deg, #3b82f6, #2563eb); }
        .report-card.monthly::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: linear-gradient(90deg, #8b5cf6, #6d28d9); }
        .report-card .label { font-size: 0.68rem; color: var(--ts); font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; display: flex; align-items: center; gap: 4px; }
        .report-card .big { font-size: 1.15rem; font-weight: 800; color: var(--tm); letter-spacing: -0.02em; }
        .report-card .sub { font-size: 0.72rem; color: var(--ts); margin-top: 4px; font-weight: 600; }
        
        .settings-card { background: var(--sf); border: 1px solid var(--br); border-radius: 18px; padding: 16px; box-shadow: var(--shadow); margin-bottom: 14px; }
        .settings-label { font-size: 0.75rem; font-weight: 800; color: var(--ts); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px; }
        .settings-input { width: 100%; padding: 10px 12px; border: 1.5px solid var(--br); border-radius: 12px; font-size: 0.9rem; font-weight: 600; color: var(--tm); background: #fff; outline: none; }
        .settings-actions { display: flex; gap: 10px; margin-top: 10px; }
        .settings-meta { font-size: 0.78rem; color: var(--ts); line-height: 1.4; margin-top: 6px; }
        .launch-badge { display: inline-flex; align-items: center; gap: 6px; border-radius: 100px; padding: 4px 10px; font-size: 0.72rem; font-weight: 800; background: var(--bg); color: var(--p); }
        .launch-badge.locked { background: #FEF3C7; color: #92400E; }
        .launch-badge.open { background: #D1FAE5; color: #065F46; }

        /* POS */
        .pos-container { display: flex; flex-direction: column; gap: 12px; }
        .pos-scanner-area { background: var(--tm); border-radius: 18px; padding: 24px 16px; text-align: center; color: white; display: flex; flex-direction: column; align-items: center; gap: 10px; box-shadow: var(--shadow); position: relative; overflow: hidden; }
        .pos-scan-btn { background: var(--g); color: white; border: none; border-radius: 100px; padding: 12px 24px; font-size: 0.95rem; font-weight: 800; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; box-shadow: 0 4px 16px rgba(61,139,95,0.4); }
        .pos-cart { background: var(--sf); border-radius: 18px; border: 1px solid var(--br); padding: 14px; box-shadow: var(--shadow); display: flex; flex-direction: column; }
        .pos-cart-items { flex: 1; overflow-y: auto; max-height: 220px; margin-bottom: 10px; }
        .pos-cart-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 0; border-bottom: 1px dashed var(--br); }
        .pci-info { flex: 1; padding-right: 8px; }
        .pci-name { font-weight: 700; font-size: 0.85rem; margin-bottom: 2px; }
        .pci-price { font-size: 0.78rem; color: var(--g); font-weight: 800; }
        .pci-qty-controls { display: flex; align-items: center; gap: 6px; background: var(--bg); border-radius: 8px; padding: 3px; border: 1px solid var(--br); }
        .pci-qty-btn { width: 26px; height: 26px; border-radius: 6px; background: var(--sf); display: flex; align-items: center; justify-content: center; font-weight: 800; border: none; cursor: pointer; }
        .pci-qty { font-weight: 800; font-size: 0.85rem; min-width: 18px; text-align: center; }
        .pci-del { color: var(--danger); background: #FEE2E2; border: none; width: 26px; height: 26px; border-radius: 6px; display: flex; align-items: center; justify-content: center; margin-left: 8px; cursor: pointer; }
        .pos-totals { border-top: 2px dashed var(--br); padding-top: 12px; }
        .pos-totals-row { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 6px; color: var(--ts); font-weight: 600; }
        .pos-totals-row.grand { font-size: 1.1rem; color: var(--tm); font-weight: 900; margin-top: 8px; }
        .pos-discounts { display: flex; gap: 6px; margin-bottom: 12px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; }
        .pos-discount-btn { padding: 5px 12px; background: var(--bg); border: 1px solid var(--br); border-radius: 100px; font-size: 0.75rem; font-weight: 800; color: var(--ts); white-space: nowrap; cursor: pointer; }
        .pos-discount-btn.active { background: var(--p); color: white; border-color: var(--p); }
        .pos-checkout-btn { width: 100%; background: linear-gradient(135deg, var(--g), var(--gp)); color: white; border: none; border-radius: 12px; padding: 14px; font-size: 1rem; font-weight: 800; margin-top: 10px; box-shadow: 0 4px 16px rgba(61,139,95,0.3); cursor: pointer; }
        .pos-checkout-btn:disabled { background: #cbd5e1; box-shadow: none; cursor: not-allowed; }
        .pos-manual-search { display: flex; gap: 8px; margin-bottom: 12px; }
        .pos-manual-search input { flex: 1; padding: 10px 12px; border: 1px solid var(--br); border-radius: 10px; outline: none; font-size: 0.88rem; }

        /* Smart Scanner */
        .scanner-overlay { position: fixed; inset: 0; z-index: 2000; background: #0a0e1a; display: none; flex-direction: column; }
        .scanner-overlay.on { display: flex; }
        .scanner-header { padding: 16px 18px 12px; display: flex; align-items: center; justify-content: space-between; background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08); }
        .scanner-header h3 { color: #fff; font-size: 1rem; font-weight: 800; display: flex; align-items: center; gap: 8px; }
        .scanner-close-btn { width: 34px; height: 34px; border-radius: 50%; background: rgba(255,255,255,0.1); border: none; color: #fff; font-size: 1rem; display: flex; align-items: center; justify-content: center; cursor: pointer; }
        .scanner-viewport { flex: 1; position: relative; overflow: hidden; background: #000; }
        .scanner-video { width: 100%; height: 100%; object-fit: cover; display: block; }
        .scanner-frame { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
        .scanner-box { width: 220px; height: 220px; position: relative; }
        .scanner-box::before, .scanner-box::after { content: ''; position: absolute; width: 28px; height: 28px; border-color: #3d8b5f; border-style: solid; }
        .scanner-box::before { top: 0; left: 0; border-width: 3px 0 0 3px; border-radius: 6px 0 0 0; }
        .scanner-box::after  { bottom: 0; right: 0; border-width: 0 3px 3px 0; border-radius: 0 0 6px 0; }
        .scanner-corner { position: absolute; width: 28px; height: 28px; border-color: #3d8b5f; border-style: solid; }
        .scanner-corner.tr { top: 0; right: 0; border-width: 3px 3px 0 0; border-radius: 0 6px 0 0; }
        .scanner-corner.bl { bottom: 0; left: 0; border-width: 0 0 3px 3px; border-radius: 0 0 0 6px; }
        .scanner-line { position: absolute; left: 4px; right: 4px; height: 2px; background: linear-gradient(90deg,transparent,#3d8b5f,transparent); animation: scanline 2s linear infinite; top: 0; }
        @keyframes scanline { 0%{top:4px;opacity:1} 90%{opacity:1} 100%{top:212px;opacity:0} }
        .scanner-status { position: absolute; bottom: 0; left: 0; right: 0; padding: 12px 16px; background: linear-gradient(to top, rgba(10,14,26,0.95), transparent); color: rgba(255,255,255,0.85); font-size: 0.8rem; font-weight: 600; text-align: center; }
        .scanner-status .status-icon { font-size: 1.1rem; margin-bottom: 2px; display: block; }
        .scanner-result-banner { position: absolute; top: 14px; left: 14px; right: 14px; background: rgba(61,139,95,0.95); color: #fff; padding: 12px 14px; border-radius: 12px; display: none; align-items: center; gap: 10px; }
        .scanner-result-banner.on { display: flex; }
        .scanner-result-banner.err { background: rgba(239,68,68,0.9); }
        .scanner-result-banner.warn { background: rgba(245,158,11,0.9); }
        .srb-icon { font-size: 1.2rem; flex-shrink: 0; }
        .srb-body { flex: 1; min-width: 0; }
        .srb-title { font-weight: 800; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .srb-sub { font-size: 0.75rem; opacity: 0.85; }
        .scanner-footer { padding: 14px 18px calc(14px + env(safe-area-inset-bottom)); background: rgba(255,255,255,0.04); border-top: 1px solid rgba(255,255,255,0.08); display: flex; gap: 10px; }
        .scan-capture-btn { flex: 1; padding: 12px; border-radius: 12px; background: rgba(255,255,255,0.12); border: 1.5px solid rgba(255,255,255,0.15); color: #fff; font-size: 0.85rem; font-weight: 800; display: flex; align-items: center; justify-content: center; gap: 6px; cursor: pointer; }
        .scan-capture-btn.primary { background: var(--g); border-color: var(--g); }

        /* Customer Intelligence */
        .ci-kpi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .ci-kpi { background: var(--sf); border-radius: 16px; padding: 16px 14px; box-shadow: var(--shadow); border-top: 3px solid transparent; border: 1px solid var(--br); }
        .ci-kpi.green  { border-top-color: #3d8b5f; }
        .ci-kpi.blue   { border-top-color: #3b82f6; }
        .ci-kpi.purple { border-top-color: #8b5cf6; }
        .ci-kpi.orange { border-top-color: #f59e0b; }
        .ci-kpi.pink   { border-top-color: #ec4899; }
        .ci-kpi.teal   { border-top-color: #14b8a6; }
        .ci-kpi.red    { border-top-color: #ef4444; }
        .ci-kpi-icon { font-size: 1.3rem; margin-bottom: 6px; }
        .ci-kpi-val  { font-size: 1.5rem; font-weight: 800; line-height: 1; margin-bottom: 4px; color: var(--tm); }
        .ci-kpi-label { font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--ts); }
        .ci-kpi-sub  { font-size: .68rem; color: var(--ts); margin-top: 2px; }
        .ci-kpi-change { font-size: .7rem; font-weight: 700; margin-top: 2px; }
        .ci-kpi-change.up   { color: #059669; }
        .ci-kpi-change.down { color: #dc2626; }
        .ci-kpi-change.neu  { color: var(--ts); }

        .period-bar { display: flex; gap: 6px; flex-wrap: wrap; background: var(--sf); padding: 8px 12px; border-radius: 14px; box-shadow: var(--shadow); margin-bottom: 16px; align-items: center; border: 1px solid var(--br); }
        .period-bar label { font-size: .78rem; font-weight: 700; color: var(--ts); margin-right: 4px; }
        .period-btn { padding: 4px 10px; border-radius: 8px; font-size: .76rem; font-weight: 600; color: var(--ts); cursor: pointer; border: 1px solid var(--br); background: var(--bg); transition: all .15s; }
        .period-btn:hover, .period-btn.active { background: var(--g); color: white; border-color: var(--g); }

        .ci-tabs { display: flex; gap: 4px; background: var(--sf); padding: 6px; border-radius: 16px; box-shadow: var(--shadow); margin-bottom: 20px; overflow-x: auto; scrollbar-width: none; border: 1px solid var(--br); }
        .ci-tabs::-webkit-scrollbar { display: none; }
        .ci-tab { padding: 7px 12px; border-radius: 10px; font-size: .78rem; font-weight: 700; color: var(--ts); cursor: pointer; transition: all .15s; border: none; background: none; white-space: nowrap; text-decoration: none; display: flex; align-items: center; gap: 4px; }
        .ci-tab.active { background: var(--g); color: white; }

        .chart-card { background: var(--sf); border-radius: 16px; box-shadow: var(--shadow); border: 1px solid var(--br); overflow: hidden; margin-bottom: 16px; }
        .chart-card-header { padding: 14px 16px; border-bottom: 1px solid var(--br); display: flex; align-items: center; justify-content: space-between; }
        .chart-card-header h6 { font-size: .88rem; font-weight: 700; color: var(--tm); margin: 0; display: flex; align-items: center; gap: 6px; }
        .chart-body { padding: 16px; position: relative; }

        .activity-feed { list-style: none; padding: 0; margin: 0; }
        .activity-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--br); font-size: 0.8rem; }
        .activity-item:last-child { border-bottom: none; }
        
        .table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
        .table th { background: #f8faff; padding: 8px 10px; text-align: left; font-weight: 700; font-size: .72rem; text-transform: uppercase; color: var(--ts); border-bottom: 1px solid var(--br); }
        .table td { padding: 8px 10px; border-bottom: 1px solid var(--br); vertical-align: middle; }

        .funnel-stage { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
        .funnel-bar-outer { flex: 1; background: #f0f4f8; border-radius: 6px; overflow: hidden; height: 36px; position: relative; }
        .funnel-bar-inner { height: 100%; border-radius: 6px; display: flex; align-items: center; padding: 0 12px; font-weight: 700; font-size: .8rem; color: white; transition: width .5s; }
        .funnel-label { min-width: 120px; font-size: .8rem; font-weight: 600; color: var(--tm); }
        .funnel-meta { min-width: 70px; text-align: right; font-size: .75rem; color: var(--ts); }
        .funnel-drop { font-size: .68rem; color: #ef4444; font-weight: 600; }

        .seg-badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 20px; font-size: .72rem; font-weight: 700; }
        .seg-vip      { background: #fef3c7; color: #92400e; }
        .seg-high     { background: #ede9fe; color: #5b21b6; }
        .seg-freq     { background: #d1fae5; color: #065f46; }
        .seg-ret      { background: #e0f2fe; color: #075985; }
        .seg-first    { background: #dbeafe; color: #1e40af; }
        .seg-window   { background: #f1f5f9; color: #475569; }
        .seg-inactive { background: #fee2e2; color: #991b1b; }
        .seg-lost     { background: #fce7f3; color: #9d174d; }

        .cohort-table { width: 100%; border-collapse: collapse; font-size: .78rem; }
        .cohort-table th { background: #f8faff; padding: 6px 8px; text-align: center; font-weight: 700; font-size: .7rem; text-transform: uppercase; color: var(--ts); }
        .cohort-table td { padding: 6px 8px; text-align: center; border: 1px solid var(--br); }
        .cohort-cell { border-radius: 4px; padding: 3px 6px; font-weight: 700; display: inline-block; min-width: 38px; }

        .insight-card { border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; display: flex; gap: 10px; align-items: flex-start; font-size: .82rem; line-height: 1.4; }
        .insight-card.info   { background: #eff6ff; border-left: 3px solid #3b82f6; }
        .insight-card.warn   { background: #fff7ed; border-left: 3px solid #f59e0b; }
        .insight-card.rec    { background: #f0fdf4; border-left: 3px solid #22c55e; }
        .insight-icon { font-size: 1.1rem; flex-shrink: 0; }
        .geo-bar { width: 100%; height: 6px; background: var(--br); border-radius: 99px; overflow: hidden; margin-top: 2px; }
        .geo-bar-fill { height: 100%; background: linear-gradient(90deg, #3d8b5f, #14b8a6); border-radius: 99px; }
    </style>
</head>
<body>

    <div class="header">
        <h1><i class="fas fa-store"></i> Store Portal</h1>
        <div class="header-profile" id="userInitial">M</div>
    </div>

    <!-- DASHBOARD VIEW -->
    <div id="view-dashboard" class="view active">
        <h2 style="font-size: 1.1rem; margin-bottom: 14px;">Overview</h2>
        <div class="stat-grid">
            <div class="stat-card primary">
                <div class="title">Today's Revenue <i class="fas fa-chart-line"></i></div>
                <div class="value" id="dashRev">ETB 0</div>
            </div>
            <div class="stat-card">
                <div class="title">Today's Orders <i class="fas fa-shopping-bag"></i></div>
                <div class="value" id="dashOrd">0</div>
            </div>
            <div class="stat-card">
                <div class="title">Pending Orders <i class="fas fa-clock"></i></div>
                <div class="value" id="dashPen" style="color: var(--warning);">0</div>
            </div>
            <div class="stat-card" onclick="switchTab('inventory')">
                <div class="title">Low Stock <i class="fas fa-exclamation-triangle"></i></div>
                <div class="value" id="dashLow" style="color: var(--danger);">0</div>
            </div>
        </div>

        <h2 style="font-size: 1rem; font-weight: 800; margin-bottom: 12px; display:flex; align-items:center; gap:6px;"><i class="fas fa-chart-bar" style="color:var(--g)"></i> Revenue Reports</h2>
        <div class="report-grid">
            <div class="report-card week">
                <div class="label"><i class="fas fa-calendar-week"></i> 7-Day</div>
                <div class="big" id="dashWeekRev">ETB 0</div>
                <div class="sub" id="dashWeekOrd">0 orders this week</div>
            </div>
            <div class="report-card biweek">
                <div class="label"><i class="fas fa-calendar-alt"></i> 14-Day</div>
                <div class="big" id="dashBiRev">ETB 0</div>
                <div class="sub" id="dashBiOrd">0 orders in 14 days</div>
            </div>
            <div class="report-card monthly" style="grid-column: 1 / -1;">
                <div class="label"><i class="fas fa-calendar"></i> 30-Day Monthly</div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div class="big" id="dashMonRev">ETB 0</div>
                    <div style="text-align:right;">
                        <div style="font-size:0.75rem;color:#8b5cf6;font-weight:800" id="dashMonOrd">0 orders</div>
                        <div style="font-size:0.7rem;color:var(--ts);margin-top:2px">last 30 days</div>
                    </div>
                </div>
            </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <h2 style="font-size: 1rem;">Recent Orders</h2>
            <span style="font-size: 0.8rem; color: var(--p); font-weight: 600; cursor: pointer;" onclick="switchTab('orders')">View All</span>
        </div>
        <div id="recentOrdersList"></div>
    </div>

    <!-- ORDERS VIEW -->
    <div id="view-orders" class="view">
        <div class="filter-scroll" id="orderFilters">
            <div class="filter-pill active" data-status="all">All</div>
            <div class="filter-pill" data-status="pending">Pending <span id="badgePending" style="display:none;background:var(--danger);color:white;border-radius:10px;padding:0 4px;font-size:0.6rem;margin-left:4px"></span></div>
            <div class="filter-pill" data-status="confirmed">Confirmed</div>
            <div class="filter-pill" data-status="out_for_delivery">Delivering</div>
            <div class="filter-pill" data-status="delivered">Delivered</div>
        </div>
        <div id="ordersList"></div>
    </div>

    <!-- INVENTORY VIEW -->
    <div id="view-inventory" class="view">
        <div style="display: flex; gap: 10px; margin-bottom: 10px;">
            <input type="text" id="searchInv" placeholder="Search products..." style="flex: 1; padding: 10px 14px; border: 1px solid var(--br); border-radius: 12px; outline: none; font-size: 0.9rem;" oninput="debounce(loadInventory, 400)()">
            <button class="btn btn-outline" onclick="toggleLowStock()"><i class="fas fa-filter"></i></button>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
            <input type="number" id="priceAdjustValue" placeholder="10" style="flex:1;padding:10px 12px;border:1px solid var(--br);border-radius:10px;min-width:100px;">
            <select id="priceAdjustMode" style="padding:10px 12px;border:1px solid var(--br);border-radius:10px;background:#fff;">
                <option value="percentage">% change</option>
                <option value="fixed">ETB change</option>
                <option value="set">Set price</option>
            </select>
            <button class="btn btn-primary" onclick="applyUniversalPriceAdjustment()">Apply to all active</button>
        </div>
        <div id="inventoryList"></div>
    </div>

    <!-- INTELLIGENCE VIEW -->
    <div id="view-intelligence" class="view">
        <h2 style="font-size: 1.1rem; font-weight: 800; margin-bottom: 14px;">Customer Intelligence</h2>

        <!-- Period Selector -->
        <div class="period-bar">
            <label><i class="fas fa-calendar-alt"></i> Period:</label>
            <button class="period-btn active" data-period="today">Today</button>
            <button class="period-btn" data-period="yesterday">Yesterday</button>
            <button class="period-btn" data-period="7d">7 Days</button>
            <button class="period-btn" data-period="30d">30 Days</button>
            <button class="period-btn" data-period="90d">90 Days</button>
            <button class="period-btn" data-period="1y">1 Year</button>
        </div>

        <!-- Dynamic KPI Strip -->
        <div class="ci-kpi-grid" id="kpiGrid">
            <div class="ci-kpi green">
                <div class="ci-kpi-icon">👥</div>
                <div class="ci-kpi-val" id="kpi-total-customers">0</div>
                <div class="ci-kpi-label">Total Customers</div>
            </div>
            <div class="ci-kpi teal">
                <div class="ci-kpi-icon">📱</div>
                <div class="ci-kpi-val" id="kpi-visits">0</div>
                <div class="ci-kpi-label">Visits</div>
                <div class="ci-kpi-sub" id="kpi-visits-sub">0 product views</div>
            </div>
            <div class="ci-kpi blue">
                <div class="ci-kpi-icon">🌟</div>
                <div class="ci-kpi-val" id="kpi-new">0</div>
                <div class="ci-kpi-label">New Customers</div>
                <div class="ci-kpi-change neu" id="kpi-new-change"></div>
            </div>
            <div class="ci-kpi purple">
                <div class="ci-kpi-icon">🔁</div>
                <div class="ci-kpi-val" id="kpi-returning">0</div>
                <div class="ci-kpi-label">Returning Customers</div>
                <div class="ci-kpi-sub" id="kpi-ret-rate"></div>
            </div>
            <div class="ci-kpi green">
                <div class="ci-kpi-icon">💰</div>
                <div class="ci-kpi-val" id="kpi-revenue">ETB 0</div>
                <div class="ci-kpi-label">Revenue (ETB)</div>
                <div class="ci-kpi-change neu" id="kpi-revenue-change"></div>
            </div>
            <div class="ci-kpi orange">
                <div class="ci-kpi-icon">📦</div>
                <div class="ci-kpi-val" id="kpi-orders">0</div>
                <div class="ci-kpi-label">Orders</div>
                <div class="ci-kpi-change neu" id="kpi-orders-change"></div>
            </div>
            <div class="ci-kpi blue">
                <div class="ci-kpi-icon">🧾</div>
                <div class="ci-kpi-val" id="kpi-aov">ETB 0</div>
                <div class="ci-kpi-label">Avg Order Value</div>
                <div class="ci-kpi-change neu" id="kpi-aov-change"></div>
            </div>
            <div class="ci-kpi pink">
                <div class="ci-kpi-icon">💎</div>
                <div class="ci-kpi-val" id="kpi-clv">ETB 0</div>
                <div class="ci-kpi-label">Customer Lifetime Value</div>
            </div>
            <div class="ci-kpi orange">
                <div class="ci-kpi-icon">🛒</div>
                <div class="ci-kpi-val" id="kpi-cart-adds">0</div>
                <div class="ci-kpi-label">Cart Additions</div>
            </div>
            <div class="ci-kpi red">
                <div class="ci-kpi-icon">⚠️</div>
                <div class="ci-kpi-val" id="kpi-abandon">0%</div>
                <div class="ci-kpi-label">Cart Abandonment</div>
            </div>
            <div class="ci-kpi teal">
                <div class="ci-kpi-icon">🔄</div>
                <div class="ci-kpi-val" id="kpi-repeat">0%</div>
                <div class="ci-kpi-label">Repeat Purchase Rate</div>
            </div>
        </div>

        <!-- Subtab Navigation -->
        <div class="ci-tabs">
            <button class="ci-tab active" data-citab="overview" onclick="switchCITab('overview')"><i class="fas fa-chart-pie"></i> Overview</button>
            <button class="ci-tab" data-citab="sales" onclick="switchCITab('sales')"><i class="fas fa-chart-line"></i> Sales</button>
            <button class="ci-tab" data-citab="funnel" onclick="switchCITab('funnel')"><i class="fas fa-filter"></i> Funnel</button>
            <button class="ci-tab" data-citab="products" onclick="switchCITab('products')"><i class="fas fa-box-open"></i> Products</button>
            <button class="ci-tab" data-citab="segments" onclick="switchCITab('segments')"><i class="fas fa-layer-group"></i> Segments</button>
            <button class="ci-tab" data-citab="cohort" onclick="switchCITab('cohort')"><i class="fas fa-th"></i> Cohort</button>
            <button class="ci-tab" data-citab="geo" onclick="switchCITab('geo')"><i class="fas fa-map-marker-alt"></i> Geographic</button>
            <button class="ci-tab" data-citab="insights" onclick="switchCITab('insights')"><i class="fas fa-lightbulb"></i> Insights</button>
            <button class="ci-tab" data-citab="customers" onclick="switchCITab('customers')"><i class="fas fa-users"></i> Customer List</button>
        </div>

        <!-- SUBTAB: OVERVIEW -->
        <div id="citab-overview" class="ci-tab-content">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-pie" style="color:#3d8b5f"></i> New vs Returning Customers</h6>
                </div>
                <div class="chart-body" style="height:220px">
                    <canvas id="pieChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-line" style="color:#3b82f6"></i> Daily Orders & Activity</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="visitsChart"></canvas>
                </div>
            </div>
        </div>

        <!-- SUBTAB: SALES -->
        <div id="citab-sales" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-area" style="color:#3d8b5f"></i> Revenue Over Time</h6>
                    <span style="font-size:.75rem;color:var(--ts)" id="salesPeriodLabel">Loading…</span>
                </div>
                <div class="chart-body" style="height:260px">
                    <canvas id="revenueChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-pie" style="color:#f59e0b"></i> Revenue by Category</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="catRevChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-trophy" style="color:#8b5cf6"></i> Top Products by Revenue</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="prodRevChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-table"></i> Daily Breakdown</h6>
                </div>
                <div class="chart-body" style="padding:0">
                    <table class="table">
                        <thead><tr><th>Date</th><th>Revenue (ETB)</th><th>Orders</th></tr></thead>
                        <tbody id="salesTableBody"><tr><td colspan="3" class="text-center py-3">Loading…</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- SUBTAB: FUNNEL -->
        <div id="citab-funnel" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-filter" style="color:#3d8b5f"></i> Conversion Funnel</h6>
                </div>
                <div class="chart-body" id="funnelWrap">
                    <div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>Loading funnel…</div>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-bar" style="color:#8b5cf6"></i> Stage Comparison</h6>
                </div>
                <div class="chart-body" style="height:260px">
                    <canvas id="funnelChart"></canvas>
                </div>
            </div>
        </div>

        <!-- SUBTAB: PRODUCTS -->
        <div id="citab-products" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-box-open" style="color:#3d8b5f"></i> Product Performance</h6>
                </div>
                <div class="chart-body" style="padding:0; overflow-x:auto;">
                    <table class="table" id="productsTable">
                        <thead>
                            <tr>
                                <th>Product</th>
                                <th>Stock</th>
                                <th>Views</th>
                                <th>Cart Adds</th>
                                <th>Orders</th>
                                <th>Qty Sold</th>
                                <th>Revenue (ETB)</th>
                                <th>Conversion</th>
                                <th>Wishlist</th>
                            </tr>
                        </thead>
                        <tbody id="productsBody">
                            <tr><td colspan="9" class="text-center py-3">Loading product data…</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-eye" style="color:#3b82f6"></i> Most Viewed Products</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="viewsChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-shopping-bag" style="color:#f59e0b"></i> Top Selling Products</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="topSalesChart"></canvas>
                </div>
            </div>
        </div>

        <!-- SUBTAB: SEGMENTS -->
        <div id="citab-segments" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-pie" style="color:#8b5cf6"></i> Segment Distribution</h6>
                </div>
                <div class="chart-body" style="height:250px">
                    <canvas id="segmentPieChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-layer-group" style="color:#3d8b5f"></i> Segment Details</h6>
                </div>
                <div class="chart-body" style="padding:0">
                    <table class="table">
                        <thead><tr><th>Segment</th><th>Customers</th><th>Revenue (ETB)</th><th>Avg Spend</th></tr></thead>
                        <tbody id="segmentBody">
                            <tr><td colspan="4" class="text-center py-3">Loading…</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-chart-bar" style="color:#f59e0b"></i> Revenue by Segment</h6>
                </div>
                <div class="chart-body" style="height:220px">
                    <canvas id="segmentRevChart"></canvas>
                </div>
            </div>
        </div>

        <!-- SUBTAB: COHORT -->
        <div id="citab-cohort" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-th" style="color:#14b8a6"></i> Monthly Cohort Retention</h6>
                </div>
                <div class="chart-body" id="cohortWrap">
                    <div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>Building cohort data…</div>
                </div>
            </div>
        </div>

        <!-- SUBTAB: GEOGRAPHIC -->
        <div id="citab-geo" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-city" style="color:#3d8b5f"></i> Orders by City</h6>
                </div>
                <div class="chart-body" id="geoTableWrap" style="padding:0">
                    <div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>Loading geographic data…</div>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-map-marked-alt" style="color:#8b5cf6"></i> Top Cities</h6>
                </div>
                <div class="chart-body" style="height:240px">
                    <canvas id="geoCityChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-map" style="color:#14b8a6"></i> Revenue by Region</h6>
                </div>
                <div class="chart-body" style="height:220px">
                    <canvas id="geoRegionChart"></canvas>
                </div>
            </div>
        </div>

        <!-- SUBTAB: INSIGHTS -->
        <div id="citab-insights" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-lightbulb" style="color:#f59e0b"></i> Key Insights</h6>
                </div>
                <div class="chart-body" id="insightsPanel">
                    <div class="text-center py-3 text-muted">Generating insights…</div>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-exclamation-triangle" style="color:#ef4444"></i> Warnings</h6>
                </div>
                <div class="chart-body" id="warningsPanel">
                    <div class="text-center py-3 text-muted">Checking for issues…</div>
                </div>
            </div>
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-star" style="color:#22c55e"></i> Recommendations</h6>
                </div>
                <div class="chart-body" id="recommendationsPanel">
                    <div class="text-center py-3 text-muted">Building recommendations…</div>
                </div>
            </div>
        </div>

        <!-- SUBTAB: CUSTOMERS -->
        <div id="citab-customers" class="ci-tab-content" style="display: none;">
            <div class="chart-card">
                <div class="chart-card-header">
                    <h6><i class="fas fa-users" style="color:#3d8b5f"></i> Customer Directory</h6>
                </div>
                <div class="chart-body" style="padding:0; overflow-x:auto;">
                    <table class="table">
                        <thead>
                            <tr>
                                <th>Customer</th>
                                <th>Level</th>
                                <th>Telegram</th>
                                <th>Orders</th>
                                <th>Spent (ETB)</th>
                                <th>Last Order</th>
                                <th>Joined</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody id="customersTableBody">
                            <tr><td colspan="8" class="text-center py-3">Loading customers…</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- DRIVERS VIEW -->
    <div id="view-drivers" class="view">
        <h2 style="font-size: 1.1rem; font-weight: 800; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center;">
            <span><i class="fas fa-map-marked-alt text-g"></i> Driver Tracking</span>
            <button class="btn btn-outline" style="padding: 6px 12px; font-size: 0.8rem;" onclick="loadDrivers()"><i class="fas fa-sync-alt"></i> Refresh</button>
        </h2>
        <div id="storeMap"></div>
        <h2 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 12px; margin-top: 20px;">Active Drivers</h2>
        <div id="driversList"></div>
    </div>

    <!-- HISTORY VIEW -->
    <div id="view-history" class="view">
        <h2 style="font-size: 1.1rem; font-weight: 800; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center;">
            <span><i class="fas fa-history text-g"></i> Sales History</span>
            <button class="btn btn-outline" style="padding: 6px 12px; font-size: 0.8rem;" onclick="loadHistory()"><i class="fas fa-sync-alt"></i> Refresh</button>
        </h2>
        <div id="historyList"></div>
    </div>

    <!-- POS VIEW -->
    <div id="view-pos" class="view">
        <h2 style="font-size: 1.1rem; font-weight: 800; margin-bottom: 14px;"><i class="fas fa-cash-register text-g"></i> POS Terminal</h2>
        <div class="pos-container">
            <div class="pos-scanner-area">
                <i class="fas fa-camera-retro" style="font-size: 2.2rem; opacity: 0.45; position:relative;z-index:1;"></i>
                <div style="font-size: 0.88rem; font-weight: 600; opacity: 0.8; position:relative;z-index:1;">Scan barcode or use visual search</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center;position:relative;z-index:1;">
                    <button class="pos-scan-btn" onclick="openSmartScanner()">
                        <i class="fas fa-qrcode"></i> Smart Scanner
                    </button>
                    <button class="pos-scan-btn" onclick="startNativeScan()" style="background:rgba(255,255,255,0.15);box-shadow:none;">
                        <i class="fas fa-external-link-alt"></i> TG Native
                    </button>
                </div>
            </div>

            <div class="pos-cart">
                <div class="pos-manual-search">
                    <input type="text" id="posSearchInput" placeholder="Enter Product ID or SKU..." onkeypress="if(event.key === 'Enter') posManualLookup()">
                    <button class="btn btn-primary" style="padding: 10px 14px; border-radius: 10px;" onclick="posManualLookup()"><i class="fas fa-search"></i></button>
                </div>

                <h3 style="font-size: 0.85rem; font-weight: 800; margin-bottom: 8px; color: var(--ts); text-transform: uppercase;">Current Sale</h3>
                
                <div class="pos-cart-items" id="posCartItems"></div>

                <div class="pos-totals">
                    <div class="pos-discounts" style="align-items: center;">
                        <button class="pos-discount-btn active" onclick="setPosDiscount(0, this)">No Discount</button>
                        <button class="pos-discount-btn" onclick="setPosDiscount(5, this)">5% Off</button>
                        <button class="pos-discount-btn" onclick="setPosDiscount(10, this)">10% Off</button>
                        <button class="pos-discount-btn" onclick="setPosDiscount(15, this)">15% Off</button>
                        <input type="number" id="customPosDiscount" placeholder="Custom %" style="width: 80px; padding: 5px 8px; border-radius: 100px; border: 1px solid var(--br); font-size: 0.78rem; outline: none;" oninput="setPosDiscount(this.value, null, true)">
                    </div>
                    
                    <div class="pos-totals-row">
                        <span>Subtotal</span>
                        <span id="posSubtotal">ETB 0</span>
                    </div>
                    <div class="pos-totals-row" style="color: var(--warning); display: none;" id="posDiscountRow">
                        <span>Discount</span>
                        <span id="posDiscountAmt">-ETB 0</span>
                    </div>
                    <div class="pos-totals-row grand">
                        <span>Total</span>
                        <span id="posTotal">ETB 0</span>
                    </div>

                    <div style="margin-bottom: 8px; margin-top: 8px;">
                        <label style="font-size: 0.78rem; font-weight: 600; color: var(--ts); margin-bottom: 4px; display: block;">Payment Method</label>
                        <select id="posPaymentMethod" style="width: 100%; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--br); font-size: 0.88rem; outline: none; background: var(--bg);">
                            <option value="cash">Cash</option>
                            <option value="cbe">CBE / Transfer</option>
                            <option value="telebirr">Telebirr</option>
                            <option value="card">Card</option>
                        </select>
                    </div>
                    <button class="pos-checkout-btn" id="posCheckoutBtn" onclick="posCheckout()" disabled>Complete Sale</button>
                </div>
            </div>
        </div>
    </div>

    <!-- SMART SCANNER MODAL -->
    <div class="scanner-overlay" id="scannerOverlay">
        <div class="scanner-header">
            <h3><i class="fas fa-qrcode" style="color:#3d8b5f"></i> Smart Scanner</h3>
            <button class="scanner-close-btn" onclick="closeSmartScanner()"><i class="fas fa-times"></i></button>
        </div>
        <div class="scanner-viewport" id="scannerViewport">
            <video id="scannerVideo" class="scanner-video" autoplay muted playsinline></video>
            <canvas id="scannerCanvas" style="display:none"></canvas>
            <div class="scanner-frame">
                <div class="scanner-box">
                    <div class="scanner-corner tr"></div>
                    <div class="scanner-corner bl"></div>
                    <div class="scanner-line" id="scannerLine"></div>
                </div>
            </div>
            <div class="scanner-result-banner" id="scannerBanner">
                <div class="srb-icon" id="scannerBannerIcon">✅</div>
                <div class="srb-body">
                    <div class="srb-title" id="scannerBannerTitle">Product Found</div>
                    <div class="srb-sub" id="scannerBannerSub"></div>
                </div>
            </div>
            <div class="scanner-status">
                <span class="status-icon" id="scanStatusIcon">📷</span>
                <div id="scanStatusText">Point camera at a barcode or QR code</div>
            </div>
        </div>
        <div class="scanner-footer">
            <button class="scan-capture-btn" onclick="captureVisualSearch()" id="captureBtn">
                <i class="fas fa-eye"></i> Visual Search
            </button>
            <button class="scan-capture-btn primary" onclick="toggleCamera()" id="flipCamBtn">
                <i class="fas fa-sync-alt"></i> Flip
            </button>
        </div>
    </div>

    <!-- SETTINGS VIEW -->
    <div id="view-settings" class="view">
        <h2 style="font-size: 1.1rem; font-weight: 800; margin-bottom: 14px;">Settings</h2>
        <div class="settings-card">
            <div class="settings-label">Launch Countdown</div>
            <div id="launchStatusBadge" class="launch-badge" style="margin-bottom:10px">Loading...</div>
            <label class="settings-label" for="launchDateInput">Launch date and time</label>
            <input id="launchDateInput" class="settings-input" type="datetime-local">
            <div class="settings-actions">
                <button class="btn btn-primary" style="flex:1" onclick="saveLaunchDate()">Save Launch Date</button>
                <button class="btn btn-outline" style="flex:1" onclick="clearLaunchDate()">Clear</button>
            </div>
            <div class="settings-meta" id="launchMeta">Set a future date to hide prices and block checkout until launch.</div>
        </div>

        <div class="settings-card" style="margin-top:14px; border: 1.5px solid rgba(61,139,95,0.25); background: linear-gradient(135deg,#f0faf4,#fff);">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <div style="width:34px;height:34px;background:linear-gradient(135deg,var(--g),var(--gp));border-radius:10px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:1rem;flex-shrink:0;">
                    <i class="fas fa-robot"></i>
                </div>
                <div>
                    <div class="settings-label" style="margin:0;font-size:0.88rem;">AI Visual Search Index</div>
                    <div style="font-size:0.72rem;color:var(--ts);font-weight:500;">CLIP + Pinecone</div>
                </div>
            </div>
            <div class="settings-meta" style="margin-bottom:12px;">
                Scans product images and builds an AI visual index so Smart Scanner can identify products by photo.
            </div>
            <div id="indexStatusBox" style="display:none;background:var(--bg);border-radius:10px;padding:10px;margin-bottom:10px;font-size:0.8rem;font-weight:600;color:var(--tm);line-height:1.5;"></div>
            <button class="btn btn-primary" id="indexBtn" style="width:100%;background:linear-gradient(135deg,var(--g),var(--gp));" onclick="runProductIndex()">
                <i class="fas fa-sync-alt"></i> Index All Products
            </button>
        </div>
    </div>

    <!-- BOTTOM NAVIGATION -->
    <div class="bottom-nav">
        <div class="nav-item active" data-tab="dashboard" onclick="switchTab('dashboard')">
            <i class="fas fa-chart-pie"></i>
            <span>Dashboard</span>
        </div>
        <div class="nav-item" data-tab="orders" onclick="switchTab('orders')">
            <i class="fas fa-list-alt"></i>
            <span>Orders</span>
            <div class="badge" id="navBadgeOrders" style="display: none;">0</div>
        </div>
        <div class="nav-item" data-tab="inventory" onclick="switchTab('inventory')">
            <i class="fas fa-box-open"></i>
            <span>Inventory</span>
        </div>
        <div class="nav-item" data-tab="drivers" onclick="switchTab('drivers')">
            <i class="fas fa-motorcycle"></i>
            <span>Drivers</span>
        </div>
        <div class="nav-item" data-tab="intelligence" onclick="switchTab('intelligence')">
            <i class="fas fa-brain"></i>
            <span>Insights</span>
        </div>
        <div class="nav-item" data-tab="history" onclick="switchTab('history')">
            <i class="fas fa-history"></i>
            <span>History</span>
        </div>
        <div class="nav-item" data-tab="pos" onclick="switchTab('pos')">
            <i class="fas fa-cash-register"></i>
            <span>POS</span>
        </div>
        <div class="nav-item" data-tab="settings" onclick="switchTab('settings')">
            <i class="fas fa-gear"></i>
            <span>Settings</span>
        </div>
    </div>

    <!-- ORDER DETAIL MODAL -->
    <div class="modal-overlay" id="orderModal" onclick="if(event.target===this) closeOrderModal()">
        <div class="modal-content">
            <div class="modal-handle"></div>
            <div id="orderModalBody"></div>
        </div>
    </div>

    <script>
        const tg = window.Telegram?.WebApp || {};
        if (tg.expand) tg.expand();

        const managerId = (tg.initDataUnsafe?.user?.id) || new URLSearchParams(window.location.search).get('manager_id') || '';
        if (tg.initDataUnsafe?.user?.first_name) {
            const initialEl = document.getElementById('userInitial');
            if (initialEl) initialEl.innerText = tg.initDataUnsafe.user.first_name.charAt(0);
        }

        let currentStatusFilter = 'all';
        let showLowStockOnly = false;
        let currentPeriod = 'today';
        let currentCITab = 'overview';
        let _timer = null;

        const COLORS = ['#3d8b5f','#3b82f6','#8b5cf6','#f59e0b','#14b8a6','#ec4899','#ef4444','#06b6d4','#84cc16','#f97316'];

        // Init
        document.addEventListener('DOMContentLoaded', () => {
            // Setup status filter pills
            document.querySelectorAll('.filter-pill').forEach(pill => {
                pill.addEventListener('click', () => {
                    document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');
                    currentStatusFilter = pill.dataset.status;
                    loadOrders();
                });
            });

            // Setup period selector buttons
            document.querySelectorAll('.period-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    currentPeriod = btn.dataset.period;
                    loadKPIs(currentPeriod);
                    switchCITab(currentCITab);
                });
            });

            // Initial load
            switchTab('dashboard');
        });

        function switchTab(tabId) {
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            
            const targetView = document.getElementById('view-' + tabId);
            const targetNav = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
            if (targetView) targetView.classList.add('active');
            if (targetNav) targetNav.classList.add('active');
            
            if (tabId === 'dashboard') loadDashboard();
            if (tabId === 'orders') loadOrders();
            if (tabId === 'inventory') loadInventory();
            if (tabId === 'history') loadHistory();
            if (tabId === 'drivers') {
                loadDrivers();
                if (storeMap) {
                    setTimeout(() => storeMap.invalidateSize(), 100);
                }
            }
            if (tabId === 'pos') renderPosCart();
            if (tabId === 'settings') loadSettings();
            if (tabId === 'intelligence') {
                loadKPIs(currentPeriod);
                switchCITab(currentCITab || 'overview');
            }
        }

        function switchCITab(tab) {
            currentCITab = tab;
            document.querySelectorAll('.ci-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.ci-tab-content').forEach(c => c.style.display = 'none');
            
            const btn = document.querySelector(`.ci-tab[data-citab="${tab}"]`);
            if (btn) btn.classList.add('active');
            const content = document.getElementById('citab-' + tab);
            if (content) content.style.display = 'block';

            if (tab === 'overview') loadOverviewCharts();
            else if (tab === 'sales') loadSalesData(currentPeriod);
            else if (tab === 'funnel') loadFunnelData(currentPeriod);
            else if (tab === 'products') loadProductsData(currentPeriod);
            else if (tab === 'segments') loadSegmentsData();
            else if (tab === 'cohort') loadCohortData();
            else if (tab === 'geo') loadGeoData(currentPeriod);
            else if (tab === 'insights') loadInsightsData();
            else if (tab === 'customers') loadCustomersData();
        }

        function createOrUpdateChart(canvasId, config) {
            if (typeof Chart === 'undefined') return null;
            const existing = Chart.getChart(canvasId);
            if (existing) existing.destroy();
            const canvas = document.getElementById(canvasId);
            if (!canvas) return null;
            return new Chart(canvas, config);
        }

        function debounce(func, wait) {
            return function() {
                clearTimeout(_timer);
                _timer = setTimeout(func, wait);
            };
        }

        function formatLaunchLocal(isoString) {
            if (!isoString) return '';
            const d = new Date(isoString);
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }

        function formatLaunchLabel(data) {
            if (!data || !data.launch_date) return data?.launch_locked ? 'Launch gate active' : 'Launch date not set';
            const when = new Date(data.launch_date).toLocaleString();
            return data.launch_locked ? `Ordering is locked until ${when}` : `Ordering is open. Last launch date was ${when}`;
        }

        // --- API CALLS ---
        async function fetchAPI(endpoint, options = {}) {
            const url = new URL('/api/v1' + endpoint, window.location.origin);
            if (managerId) url.searchParams.append('manager_id', managerId);
            url.searchParams.append('t', Date.now());
            
            const reqOpts = { ...options };
            if (options.body && typeof options.body === 'string') {
                reqOpts.headers = { ...reqOpts.headers, 'Content-Type': 'application/json' };
            }
            try {
                const res = await fetch(url, reqOpts);
                const data = await res.json();
                if (data.success) return data.data;
                if (tg.showAlert) tg.showAlert(data.message || 'Error');
                else alert(data.message || 'Error');
                return null;
            } catch(e) {
                console.error('API Error:', e);
                return null;
            }
        }

        function fmtRev(n) {
            if (n >= 1000000) return 'ETB ' + (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return 'ETB ' + (n / 1000).toFixed(1) + 'K';
            return 'ETB ' + Math.round(n || 0).toLocaleString();
        }

        function fmtNum(n) {
            if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n/1000).toFixed(1) + 'K';
            return Math.round(n || 0).toLocaleString();
        }

        function fmtChange(change, el) {
            if (!el) return;
            if (change === null || change === undefined) { el.textContent = ''; return; }
            const cls = change > 0 ? 'up' : change < 0 ? 'down' : 'neu';
            const icon = change > 0 ? '↑' : change < 0 ? '↓' : '→';
            el.className = 'ci-kpi-change ' + cls;
            el.textContent = `${icon} ${Math.abs(change)}% vs prev period`;
        }

        async function loadDashboard() {
            const data = await fetchAPI('/store/dashboard');
            if (!data) return;
            
            document.getElementById('dashRev').innerText = `ETB ${Math.round(data.today.revenue).toLocaleString()}`;
            document.getElementById('dashOrd').innerText = data.today.orders;
            document.getElementById('dashPen').innerText = data.pending_orders;
            document.getElementById('dashLow').innerText = data.low_stock_products.length;

            document.getElementById('dashWeekRev').innerText = fmtRev(data.week?.revenue || 0);
            document.getElementById('dashWeekOrd').innerText = `${data.week?.orders || 0} orders this week`;

            document.getElementById('dashBiRev').innerText = fmtRev(data.bi_weekly?.revenue || 0);
            document.getElementById('dashBiOrd').innerText = `${data.bi_weekly?.orders || 0} orders in 14 days`;

            document.getElementById('dashMonRev').innerText = fmtRev(data.monthly?.revenue || 0);
            document.getElementById('dashMonOrd').innerText = `${data.monthly?.orders || 0} orders`;

            const b1 = document.getElementById('navBadgeOrders');
            const b2 = document.getElementById('badgePending');
            if (data.pending_orders > 0) {
                if (b1) { b1.style.display = 'block'; b1.innerText = data.pending_orders; }
                if (b2) { b2.style.display = 'inline-block'; b2.innerText = data.pending_orders; }
            } else {
                if (b1) b1.style.display = 'none';
                if (b2) b2.style.display = 'none';
            }

            const rList = document.getElementById('recentOrdersList');
            if (rList) {
                if (data.recent_orders.length === 0) {
                    rList.innerHTML = '<div style="text-align:center;padding:20px;color:var(--ts)">No recent orders</div>';
                } else {
                    rList.innerHTML = data.recent_orders.map(o => createOrderCard(o)).join('');
                }
            }
        }

        async function loadOrders() {
            const data = await fetchAPI(`/store/orders?status=${currentStatusFilter}`);
            if (!data) return;
            
            const list = document.getElementById('ordersList');
            if (!list) return;
            if (data.orders.length === 0) {
                list.innerHTML = `<div style="text-align:center;padding:40px 20px;color:var(--ts)"><i class="fas fa-inbox" style="font-size:3rem;margin-bottom:10px;opacity:0.3"></i><br>No orders found</div>`;
            } else {
                list.innerHTML = data.orders.map(o => createOrderCard(o)).join('');
            }
        }

        function createOrderCard(o) {
            const discBadge = (o.discount_amount > 0)
                ? `<span style="background:#d1fae5;color:#065f46;font-size:0.65rem;font-weight:800;padding:2px 7px;border-radius:100px;border:1px solid #a7f3d0;margin-left:6px">-ETB ${Math.round(o.discount_amount).toLocaleString()}</span>`
                : '';
            return `
            <div class="order-card" onclick="openOrderModal(${o.id})">
                <div class="order-header">
                    <div class="order-id">#${o.order_number}${discBadge}</div>
                    <div class="order-status status-${o.status}">${o.status_label}</div>
                </div>
                <div class="order-customer">
                    <div class="customer-avatar"><i class="fas fa-user"></i></div>
                    <div class="customer-info">
                        <div class="name">${o.customer}</div>
                        <div class="phone">${o.customer_phone || ''} <span style="color:var(--ts);font-size:0.7rem">• ${o.created_label || o.created_at}</span></div>
                    </div>
                </div>
                <div class="order-footer">
                    <div class="order-items">${o.items_count || ''} Items</div>
                    <div class="order-total">ETB ${Math.round(o.total).toLocaleString()}</div>
                </div>
            </div>`;
        }

        async function loadSettings() {
            const data = await fetchAPI('/store/settings');
            if (!data) return;

            const input = document.getElementById('launchDateInput');
            if (input) input.value = formatLaunchLocal(data.launch_date);

            const badge = document.getElementById('launchStatusBadge');
            if (badge) {
                badge.className = 'launch-badge ' + (data.launch_locked ? 'locked' : 'open');
                badge.textContent = data.launch_locked ? 'Locked until launch' : (data.launch_date ? 'Launch open' : 'No launch date set');
            }

            const meta = document.getElementById('launchMeta');
            if (meta) meta.textContent = formatLaunchLabel(data);
        }

        async function saveLaunchDate() {
            const input = document.getElementById('launchDateInput');
            if (!input || !input.value) {
                alert('Pick a date and time first');
                return;
            }
            await fetchAPI('/store/launch-date', { method: 'POST', body: JSON.stringify({ launch_date: new Date(input.value).toISOString() }) });
            await loadSettings();
            await loadDashboard();
        }

        async function clearLaunchDate() {
            await fetchAPI('/store/launch-date', { method: 'DELETE' });
            const input = document.getElementById('launchDateInput');
            if (input) input.value = '';
            await loadSettings();
            await loadDashboard();
        }

        async function updatePrice(id, currentPrice) {
            const newPrice = prompt(`Enter new price for this product:`, currentPrice);
            if (newPrice === null || newPrice === '') return;
            const price = parseFloat(newPrice);
            if (isNaN(price) || price < 0) {
                alert('Invalid price');
                return;
            }
            const res = await fetchAPI(`/store/products/${id}/price`, {
                method: 'POST',
                body: JSON.stringify({ price: price })
            });
            if (res) loadInventory();
        }

        async function loadInventory() {
            const search = document.getElementById('searchInv')?.value || '';
            const ls = showLowStockOnly ? '1' : '0';
            const data = await fetchAPI(`/store/products?search=${encodeURIComponent(search)}&low_stock=${ls}`);
            if (!data) return;
            
            const list = document.getElementById('inventoryList');
            if (!list) return;
            if (data.products.length === 0) {
                list.innerHTML = `<div style="text-align:center;padding:40px 20px;color:var(--ts)">No products found</div>`;
            } else {
                list.innerHTML = data.products.map(p => `
                <div class="product-item">
                    <img src="${p.image || ''}" class="product-img" onerror="this.src=''">
                    <div class="product-info">
                        <div class="product-name">${p.name}</div>
                        <div class="product-stock">
                            <span style="color:var(--p); cursor:pointer;" onclick="updatePrice(${p.id}, ${p.price})" title="Click to edit price">ETB ${p.price} <i class="fas fa-pencil-alt" style="font-size:0.7rem;margin-left:2px;"></i></span> • 
                            <span class="stock-badge ${p.low_stock ? 'low' : 'good'}">${p.stock} in stock</span>
                        </div>
                    </div>
                    <div class="stock-actions">
                        <button class="stock-btn" onclick="updateStock(${p.id}, ${p.stock - 1})">-</button>
                        <button class="stock-btn" onclick="updateStock(${p.id}, ${p.stock + 1})">+</button>
                    </div>
                </div>`).join('');
            }
        }

        function toggleLowStock() {
            showLowStockOnly = !showLowStockOnly;
            loadInventory();
        }

        async function applyUniversalPriceAdjustment() {
            const valueInput = document.getElementById('priceAdjustValue');
            const modeInput = document.getElementById('priceAdjustMode');
            if (!valueInput || !modeInput) return;

            const rawValue = valueInput.value;
            if (rawValue === '' || Number.isNaN(Number(rawValue))) {
                alert('Enter a valid number first');
                return;
            }

            const data = await fetchAPI('/store/products/price-adjustment', {
                method: 'POST',
                body: JSON.stringify({
                    mode: modeInput.value,
                    value: Number(rawValue),
                })
            });

            if (data) {
                alert(data.updated_count ? `Updated ${data.updated_count} active products` : 'No active products were updated');
                await loadInventory();
            }
        }

        async function updateStock(id, newStock) {
            if (newStock < 0) return;
            await fetchAPI(`/store/products/${id}/stock`, { method: 'POST', body: JSON.stringify({stock: newStock}) });
            loadInventory();
        }

        // MAP & DRIVERS
        let storeMap = null;
        let driverMarkers = [];
        
        function initStoreMap() {
            if (storeMap || typeof L === 'undefined') return;
            const container = document.getElementById('storeMap');
            if (!container) return;
            storeMap = L.map('storeMap').setView([9.03, 38.74], 12);
            L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
            }).addTo(storeMap);
        }

        async function loadDrivers() {
            const data = await fetchAPI('/store/drivers');
            if (!data) return;
            
            initStoreMap();
            if (storeMap) {
                driverMarkers.forEach(m => storeMap.removeLayer(m));
                driverMarkers = [];
            }
            
            let bounds = [];
            const list = document.getElementById('driversList');
            if (!list) return;

            if (data.drivers.length === 0) {
                list.innerHTML = `<div style="text-align:center;padding:40px 20px;color:var(--ts)">No active drivers</div>`;
            } else {
                list.innerHTML = data.drivers.map(d => {
                    if (d.lat && d.lng && storeMap) {
                        const markerColor = d.active_deliveries > 0 ? '#F59E0B' : '#10B981';
                        const m = L.circleMarker([d.lat, d.lng], {
                            radius: 10, fillColor: markerColor, color: '#fff', weight: 2, fillOpacity: 0.9
                        }).addTo(storeMap);
                        
                        m.bindPopup(`
                            <div style="text-align:center;font-family:'Outfit',sans-serif;">
                                <div style="font-weight:800;font-size:0.95rem;color:var(--tm);margin-bottom:4px;">${d.name}</div>
                                <div style="font-size:0.78rem;color:var(--ts);margin-bottom:6px;"><i class="fas fa-phone"></i> ${d.phone || 'No phone'}</div>
                                <div style="font-weight:800;font-size:0.8rem;color:${d.active_deliveries > 0 ? 'var(--warning)' : 'var(--g)'}">
                                    ${d.active_deliveries > 0 ? d.active_deliveries + ' Active Orders' : 'Available'}
                                </div>
                            </div>
                        `);
                        driverMarkers.push(m);
                        bounds.push([d.lat, d.lng]);
                    }
                    
                    return `
                    <div class="driver-item" onclick="if(storeMap && ${d.lat} && ${d.lng}) { storeMap.setView([${d.lat}, ${d.lng}], 15); window.scrollTo(0,0); }">
                        <div class="driver-info">
                            <div class="driver-avatar" style="${d.active_deliveries > 0 ? 'background:#fef3c7;color:#d97706;border-color:#fde68a;' : 'background:#d1fae5;color:#059669;border-color:#a7f3d0;'}"><i class="fas fa-motorcycle"></i></div>
                            <div>
                                <div style="font-weight:800;font-size:0.9rem;color:var(--tm);">${d.name}</div>
                                <div style="font-size:0.78rem;color:var(--ts);font-weight:500;">${d.phone || 'No phone'}</div>
                            </div>
                        </div>
                        <div style="text-align:right">
                            <div style="font-size:0.78rem;font-weight:800;color:${d.active_deliveries > 0 ? 'var(--warning)' : 'var(--g)'}">${d.active_deliveries > 0 ? d.active_deliveries + ' Active' : 'Available'}</div>
                        </div>
                    </div>`;
                }).join('');
            }
            
            if (storeMap) {
                if (bounds.length > 0) storeMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
                else storeMap.setView([9.03, 38.74], 12);
            }
        }

        // ORDER MODAL
        let currentOrder = null;
        async function openOrderModal(orderId) {
            currentOrder = await fetchAPI(`/store/orders/${orderId}`);
            if (!currentOrder) return;
            
            const b = document.getElementById('orderModalBody');
            if (!b) return;
            
            let actionBtns = '';
            if (currentOrder.status === 'pending') {
                actionBtns = `
                <div style="display:flex;gap:10px;margin-top:16px;">
                    <button class="btn btn-outline" style="flex:1" onclick="updateOrderStatus('cancelled')">Reject</button>
                    <button class="btn btn-primary" style="flex:1" onclick="updateOrderStatus('confirmed')">Confirm Order</button>
                </div>`;
            } else if (currentOrder.status === 'confirmed' || currentOrder.status === 'packed') {
                actionBtns = `
                <div style="margin-top:16px;">
                    <button class="btn btn-primary" style="width:100%;margin-bottom:8px;" onclick="showAssignDriver()">Assign Driver</button>
                    ${currentOrder.status === 'confirmed' ? `<button class="btn btn-outline" style="width:100%;" onclick="updateOrderStatus('packed')">Mark as Packed</button>` : ''}
                </div>`;
            }

            let deliverySection = '';
            if (currentOrder.delivery) {
                deliverySection = `
                <div style="background:var(--bg);padding:10px 12px;border-radius:12px;margin:14px 0;border:1px solid var(--br)">
                    <div style="font-size:0.72rem;color:var(--ts);margin-bottom:2px">Delivery Assignment</div>
                    <div style="font-weight:700;font-size:0.88rem">${currentOrder.delivery.driver_name}</div>
                    <div style="font-size:0.78rem;color:var(--ts)">Status: <span style="text-transform:capitalize">${currentOrder.delivery.status.replace('_', ' ')}</span></div>
                </div>`;
            }

            b.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;">
                    <div>
                        <div style="font-size:1.1rem;font-weight:800;">Order #${currentOrder.order_number}</div>
                        <div style="font-size:0.78rem;color:var(--ts);">${new Date(currentOrder.created_at).toLocaleString()}</div>
                    </div>
                    <div class="order-status status-${currentOrder.status}">${currentOrder.status_label}</div>
                </div>

                <div style="display:flex;gap:10px;margin-bottom:16px;">
                    <div style="width:36px;height:36px;border-radius:50%;background:var(--p);color:white;display:flex;align-items:center;justify-content:center"><i class="fas fa-user"></i></div>
                    <div>
                        <div style="font-weight:700;font-size:0.9rem;">${currentOrder.customer}</div>
                        <div style="font-size:0.78rem;color:var(--ts);">${currentOrder.customer_phone}</div>
                    </div>
                </div>

                <div style="background:var(--bg);padding:10px 12px;border-radius:12px;margin-bottom:14px;font-size:0.82rem;">
                    <div style="color:var(--ts);margin-bottom:2px"><i class="fas fa-map-marker-alt"></i> Delivery Address</div>
                    <div style="font-weight:600">${currentOrder.location || 'Not provided'}</div>
                </div>

                ${deliverySection}

                <div style="font-weight:800;font-size:0.85rem;margin-bottom:8px;">Order Items</div>
                <div style="border:1px solid var(--br);border-radius:12px;padding:0 10px;margin-bottom:14px;">
                    ${currentOrder.items.map(i => `
                    <div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--br);">
                        <img src="${i.product_image || ''}" style="width:36px;height:36px;border-radius:6px;object-fit:cover;background:var(--bg)">
                        <div style="flex:1;">
                            <div style="font-size:0.82rem;font-weight:600">${i.product_name}</div>
                            <div style="font-size:0.72rem;color:var(--ts)">x${i.quantity} • ETB ${i.unit_price}</div>
                        </div>
                        <div style="font-weight:700;font-size:0.85rem">ETB ${i.total_price}</div>
                    </div>`).join('')}
                </div>

                <div style="margin-bottom:16px;">
                    <div class="detail-row"><span>Subtotal</span><span>ETB ${currentOrder.subtotal.toLocaleString()}</span></div>
                    ${currentOrder.spending_discount_amount > 0 ? `<div class="detail-row"><span style="color:var(--g)">🏷️ Spending Discount</span><span style="color:var(--g)">-ETB ${Math.round(currentOrder.spending_discount_amount).toLocaleString()}</span></div>` : ''}
                    ${currentOrder.qty_discount_amount_saved > 0 ? `<div class="detail-row"><span style="color:var(--g)">📦 Multi-Buy Discount</span><span style="color:var(--g)">-ETB ${Math.round(currentOrder.qty_discount_amount_saved).toLocaleString()}</span></div>` : ''}
                    <div class="detail-row"><span>Delivery Fee</span><span>ETB ${currentOrder.delivery_fee.toLocaleString()}</span></div>
                    <div class="detail-row" style="font-size:1rem;font-weight:800;border:none;margin-top:4px;"><span>Total</span><span style="color:var(--p)">ETB ${currentOrder.total.toLocaleString()}</span></div>
                </div>

                ${actionBtns}
            `;
            
            document.getElementById('orderModal')?.classList.add('active');
        }

        function closeOrderModal() {
            document.getElementById('orderModal')?.classList.remove('active');
            currentOrder = null;
        }

        async function updateOrderStatus(status) {
            if (!confirm(`Change order status to ${status}?`)) return;
            await fetchAPI(`/store/orders/${currentOrder.id}/status`, { method: 'POST', body: JSON.stringify({status}) });
            closeOrderModal();
            loadOrders();
        }

        async function showAssignDriver() {
            const data = await fetchAPI('/store/drivers');
            if (!data || data.drivers.length === 0) {
                alert("No drivers available"); return;
            }
            
            const b = document.getElementById('orderModalBody');
            if (!b) return;
            b.innerHTML = `
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
                    <button class="btn btn-outline" style="padding:4px 10px;" onclick="openOrderModal(${currentOrder.id})"><i class="fas fa-arrow-left"></i></button>
                    <h3 style="margin:0;font-size:1rem">Assign Driver</h3>
                </div>
                <div style="margin-bottom:10px;font-size:0.8rem;color:var(--ts)">Select a driver for Order #${currentOrder.order_number}</div>
                <div>
                    ${data.drivers.map(d => `
                    <div class="driver-item" onclick="assignDriver(${d.id})" style="cursor:pointer;border-color:var(--p);">
                        <div class="driver-info">
                            <div class="driver-avatar"><i class="fas fa-motorcycle"></i></div>
                            <div>
                                <div style="font-weight:700;font-size:0.88rem">${d.name}</div>
                                <div style="font-size:0.75rem;color:var(--ts)">${d.active_deliveries} Active Orders</div>
                            </div>
                        </div>
                        <i class="fas fa-chevron-right" style="color:var(--ts)"></i>
                    </div>`).join('')}
                </div>
            `;
        }

        async function assignDriver(driverId) {
            await fetchAPI(`/store/orders/${currentOrder.id}/assign-driver`, { method: 'POST', body: JSON.stringify({driver_id: driverId}) });
            alert("Driver assigned successfully!");
            closeOrderModal();
            loadOrders();
        }

        // HISTORY
        async function loadHistory() {
            const data = await fetchAPI('/store/sales/history');
            const container = document.getElementById('historyList');
            if (!container) return;
            if (!data || !data.history || data.history.length === 0) {
                container.innerHTML = '<div style="text-align:center; padding:30px 0; color:var(--ts);">No history available.</div>';
                return;
            }
            container.innerHTML = data.history.map(item => {
                const isOnline = item.type === 'online';
                const badgeColor = isOnline ? 'var(--p)' : 'var(--g)';
                const badgeText = isOnline ? 'Online' : 'In-Store';
                const canCancel = item.status !== 'cancelled' && item.status !== 'refunded' && item.status !== 'voided';
                
                return `
                    <div style="background:var(--sf); border-radius:14px; padding:14px; margin-bottom:10px; border:1px solid var(--br); box-shadow:var(--shadow);">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                            <div>
                                <div style="font-size:0.7rem; font-weight:800; color:white; background:${badgeColor}; padding:2px 7px; border-radius:100px; display:inline-block; margin-bottom:4px;">${badgeText}</div>
                                <div style="font-weight:700; font-size:0.9rem;">${item.reference}</div>
                            </div>
                            <div style="text-align:right;">
                                <div style="font-weight:800; color:var(--tm); font-size:0.95rem;">ETB ${item.total}</div>
                                <div style="font-size:0.72rem; color:var(--ts); text-transform:capitalize;">${item.payment_method} · ${item.status}</div>
                            </div>
                        </div>
                        <div style="font-size:0.72rem; color:var(--ts); display:flex; justify-content:space-between; align-items:center;">
                            <span>${new Date(item.created_at).toLocaleString()}</span>
                            ${canCancel ? `<button class="btn" style="background:#FEE2E2; color:var(--danger); font-size:0.72rem; padding:3px 8px; border-radius:6px;" onclick="cancelSale('${item.type}', '${item.id}')">Cancel</button>` : ''}
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function cancelSale(type, id) {
            if (!confirm('Are you sure you want to cancel this sale? Stock will be restored.')) return;
            const res = await fetchAPI('/store/sales/cancel', {
                method: 'POST',
                body: JSON.stringify({ type, id })
            });
            if (res) {
                alert(res.message || 'Cancelled successfully');
                loadHistory();
                loadDashboard();
            }
        }

        // POS LOGIC
        let posCart = [];
        let posDiscountPercent = 0;

        function startNativeScan() {
            if (window.Telegram?.WebApp?.showScanQrPopup) {
                window.Telegram.WebApp.showScanQrPopup({ text: "Scan Product QR Code to add to Cart" }, function(result) {
                    if (result) {
                        lookupProductForPos(result);
                        return true;
                    }
                });
            } else {
                openSmartScanner();
            }
        }

        let _scanStream = null;
        let _scanActive = false;
        let _scanLock = false;
        let _scanFacingMode = 'environment';
        let _zxing = null;

        async function _loadZxing() {
            if (_zxing) return _zxing;
            await new Promise((resolve, reject) => {
                if (window.ZXing) { resolve(); return; }
                const s = document.createElement('script');
                s.src = 'https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js';
                s.onload = resolve;
                s.onerror = reject;
                document.head.appendChild(s);
            });
            const hints = new Map();
            hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [
                ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8,
                ZXing.BarcodeFormat.UPC_A,  ZXing.BarcodeFormat.UPC_E,
                ZXing.BarcodeFormat.CODE_128, ZXing.BarcodeFormat.CODE_39,
                ZXing.BarcodeFormat.QR_CODE
            ]);
            hints.set(ZXing.DecodeHintType.TRY_HARDER, true);
            _zxing = new ZXing.BrowserMultiFormatReader(hints, 400);
            return _zxing;
        }

        async function openSmartScanner() {
            _scanActive = true;
            _scanLock = false;
            document.getElementById('scannerOverlay')?.classList.add('on');
            setScanStatus('📷', 'Activating camera…');
            _hideScannerBanner();
            await _startCamera();
            _startBarcodeLoop();
        }

        function closeSmartScanner() {
            _scanActive = false;
            document.getElementById('scannerOverlay')?.classList.remove('on');
            _stopCamera();
        }

        async function _startCamera() {
            _stopCamera();
            const video = document.getElementById('scannerVideo');
            if (!video) return;
            try {
                _scanStream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: _scanFacingMode, width: { ideal: 1280 }, height: { ideal: 720 } }
                });
                video.srcObject = _scanStream;
                await video.play();
                setScanStatus('🔍', 'Scanning for barcode…');
            } catch(e) {
                setScanStatus('⚠️', 'Camera permission denied. Use Visual Search or manual entry.');
            }
        }

        function _stopCamera() {
            if (_scanStream) { _scanStream.getTracks().forEach(t => t.stop()); _scanStream = null; }
            if (_zxing) { try { _zxing.reset(); } catch(e) {} }
        }

        async function toggleCamera() {
            _scanFacingMode = _scanFacingMode === 'environment' ? 'user' : 'environment';
            await _startCamera();
            _startBarcodeLoop();
        }

        function setScanStatus(icon, text) {
            const iEl = document.getElementById('scanStatusIcon');
            const tEl = document.getElementById('scanStatusText');
            if (iEl) iEl.textContent = icon;
            if (tEl) tEl.textContent = text;
        }

        function _showScannerBanner(type, icon, title, sub) {
            const b = document.getElementById('scannerBanner');
            if (!b) return;
            b.className = 'scanner-result-banner on' + (type === 'err' ? ' err' : type === 'warn' ? ' warn' : '');
            const i = document.getElementById('scannerBannerIcon');
            const t = document.getElementById('scannerBannerTitle');
            const s = document.getElementById('scannerBannerSub');
            if (i) i.textContent = icon;
            if (t) t.textContent = title;
            if (s) s.textContent = sub;
        }

        function _hideScannerBanner() {
            const b = document.getElementById('scannerBanner');
            if (b) b.className = 'scanner-result-banner';
        }

        async function _startBarcodeLoop() {
            const video = document.getElementById('scannerVideo');
            const canvas = document.getElementById('scannerCanvas');
            if (!video || !canvas) return;
            const ctx = canvas.getContext('2d');
            let reader;
            try {
                reader = await _loadZxing();
            } catch(e) {
                setScanStatus('⚠️', 'Barcode library unavailable — use Visual Search.');
                return;
            }

            const tick = async () => {
                if (!_scanActive || _scanLock) return;
                if (video.readyState < 2) { requestAnimationFrame(tick); return; }
                canvas.width  = video.videoWidth  || 640;
                canvas.height = video.videoHeight || 480;
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                try {
                    const luminanceSource = new ZXing.HTMLCanvasElementLuminanceSource(canvas);
                    const binaryBitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(luminanceSource));
                    const result = reader.decodeBitmap(binaryBitmap);
                    if (result && result.getText()) {
                        _scanLock = true;
                        const code = result.getText();
                        setScanStatus('✅', `Code detected: ${code}`);
                        await _handleScanResult(code);
                    }
                } catch(e) {}
                if (_scanActive && !_scanLock) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        }

        async function _handleScanResult(code) {
            _showScannerBanner('', '⏳', 'Looking up product…', code);
            setScanStatus('⏳', 'Fetching product details…');
            try {
                const data = await fetchAPI(`/store/pos/lookup-product?q=${encodeURIComponent(code)}`);
                if (data && data.product) {
                    const p = data.product;
                    _showScannerBanner('', '✅', p.name, `ETB ${p.price.toLocaleString()} · Stock: ${p.stock_qty}`);
                    setScanStatus('✅', 'Product added to cart!');
                    addToPosCart(p);
                    setTimeout(() => closeSmartScanner(), 1500);
                } else {
                    _showScannerBanner('warn', '⚠️', 'Barcode not matched — trying visual search…', code);
                    setScanStatus('🔍', 'Falling back to visual search…');
                    await _captureAndVisualSearch();
                }
            } catch(e) {
                _showScannerBanner('err', '❌', 'Network error', 'Check connection');
                _scanLock = false;
            }
        }

        async function captureVisualSearch() {
            if (!_scanStream) { alert('Camera not active.'); return; }
            _showScannerBanner('', '⏳', 'Analyzing image…', 'AI visual search in progress');
            setScanStatus('🤖', 'Running visual AI search…');
            await _captureAndVisualSearch();
        }

        async function _captureAndVisualSearch() {
            const video = document.getElementById('scannerVideo');
            const canvas = document.getElementById('scannerCanvas');
            if (!video || !canvas) return;
            const ctx = canvas.getContext('2d');
            canvas.width  = video.videoWidth  || 640;
            canvas.height = video.videoHeight || 480;
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

            canvas.toBlob(async (blob) => {
                const form = new FormData();
                form.append('image', blob, 'capture.jpg');
                if (managerId) form.append('manager_id', managerId);
                try {
                    const res = await fetch('/api/v1/store/pos/visual-search', { method: 'POST', body: form });
                    const data = await res.json();
                    if (data.success && data.data && data.data.product) {
                        const p = data.data.product;
                        _showScannerBanner('', '🤖', p.name, `ETB ${p.price.toLocaleString()}`);
                        setScanStatus('✅', 'Visual match found — added to cart!');
                        addToPosCart(p);
                        setTimeout(() => closeSmartScanner(), 1500);
                    } else {
                        _showScannerBanner('err', '😕', 'No match found', data.message || 'Try manual search');
                        setScanStatus('❌', 'No product matched — try manual search');
                        _scanLock = false;
                    }
                } catch(e) {
                    _showScannerBanner('err', '❌', 'Visual search error', 'Check connection');
                    _scanLock = false;
                }
            }, 'image/jpeg', 0.85);
        }

        function posManualLookup() {
            const input = document.getElementById('posSearchInput');
            if (!input) return;
            const query = input.value.trim();
            if (!query) return;
            input.value = '';
            lookupProductForPos(query);
        }

        async function lookupProductForPos(query) {
            const data = await fetchAPI(`/store/pos/lookup-product?q=${encodeURIComponent(query)}`);
            if (data && data.product) {
                addToPosCart(data.product);
            } else {
                alert('Product not found');
            }
        }

        function addToPosCart(product) {
            const existing = posCart.find(item => item.id === product.id);
            if (existing) {
                if (existing.quantity >= product.stock_qty) {
                    alert(`Only ${product.stock_qty} in stock!`);
                    return;
                }
                existing.quantity++;
            } else {
                if (product.stock_qty < 1) {
                    alert('Out of stock!');
                    return;
                }
                posCart.push({ ...product, quantity: 1 });
            }
            renderPosCart();
        }

        function updatePosQty(index, change) {
            const item = posCart[index];
            if (!item) return;
            const newQty = item.quantity + change;
            if (newQty < 1) return;
            if (newQty > item.stock_qty) {
                alert(`Maximum stock available: ${item.stock_qty}`);
                return;
            }
            item.quantity = newQty;
            renderPosCart();
        }

        function removeFromPos(index) {
            posCart.splice(index, 1);
            renderPosCart();
        }

        function setPosDiscount(percent, btnElement, isCustom=false) {
            posDiscountPercent = parseFloat(percent) || 0;
            if (posDiscountPercent > 100) posDiscountPercent = 100;
            if (posDiscountPercent < 0) posDiscountPercent = 0;
            
            document.querySelectorAll('.pos-discount-btn').forEach(btn => btn.classList.remove('active'));
            if (btnElement) {
                btnElement.classList.add('active');
                const customEl = document.getElementById('customPosDiscount');
                if (customEl) customEl.value = '';
            }
            renderPosCart();
        }

        function renderPosCart() {
            const container = document.getElementById('posCartItems');
            let subtotal = 0;
            if (!container) return;
            
            if (posCart.length === 0) {
                container.innerHTML = '<div style="text-align:center; padding:30px 0; color:var(--ts); font-size:0.85rem;"><i class="fas fa-shopping-basket" style="font-size:2rem; opacity:0.3; margin-bottom:8px;"></i><br>Cart is empty</div>';
            } else {
                container.innerHTML = posCart.map((item, idx) => {
                    subtotal += (item.price * item.quantity);
                    return `
                        <div class="pos-cart-item">
                            <div class="pci-info">
                                <div class="pci-name">${item.name}</div>
                                <div class="pci-price">ETB ${item.price.toLocaleString()}</div>
                            </div>
                            <div class="pci-qty-controls">
                                <button class="pci-qty-btn" onclick="updatePosQty(${idx}, -1)"><i class="fas fa-minus"></i></button>
                                <div class="pci-qty">${item.quantity}</div>
                                <button class="pci-qty-btn" onclick="updatePosQty(${idx}, 1)"><i class="fas fa-plus"></i></button>
                            </div>
                            <button class="pci-del" onclick="removeFromPos(${idx})"><i class="fas fa-trash-alt"></i></button>
                        </div>
                    `;
                }).join('');
            }
            
            const discountAmt = subtotal * (posDiscountPercent / 100);
            const total = subtotal - discountAmt;
            
            const subEl = document.getElementById('posSubtotal');
            if (subEl) subEl.textContent = `ETB ${subtotal.toLocaleString()}`;
            
            const discRow = document.getElementById('posDiscountRow');
            if (discRow) {
                if (posDiscountPercent > 0) {
                    discRow.style.display = 'flex';
                    const amtEl = document.getElementById('posDiscountAmt');
                    if (amtEl) amtEl.textContent = `-ETB ${discountAmt.toLocaleString()}`;
                } else {
                    discRow.style.display = 'none';
                }
            }
            
            const totEl = document.getElementById('posTotal');
            if (totEl) totEl.textContent = `ETB ${total.toLocaleString()}`;
            
            const btnEl = document.getElementById('posCheckoutBtn');
            if (btnEl) btnEl.disabled = (posCart.length === 0);
        }

        async function posCheckout() {
            if (posCart.length === 0) return;
            const btn = document.getElementById('posCheckoutBtn');
            if (!btn) return;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
            
            try {
                const payload = {
                    manager_id: managerId,
                    items: posCart.map(item => ({ product_id: item.id, quantity: item.quantity, unit_price: item.price })),
                    discount_percentage: posDiscountPercent,
                    payment_method: document.getElementById('posPaymentMethod')?.value || 'cash'
                };
                
                const res = await fetch('/api/v1/store/pos/checkout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                const data = await res.json();
                if (data.success) {
                    alert(`Sale Completed Successfully!\nTotal: ETB ${data.data.total}\nItems: ${data.data.items_count}`);
                    posCart = [];
                    setPosDiscount(0, document.querySelector('.pos-discount-btn'));
                    renderPosCart();
                } else {
                    alert(data.message || 'Checkout failed');
                    btn.disabled = false;
                    btn.textContent = 'Complete Sale';
                }
            } catch(e) {
                alert('Checkout error');
                btn.disabled = false;
                btn.textContent = 'Complete Sale';
            }
        }

        // INDEXING
        async function runProductIndex() {
            const btn = document.getElementById('indexBtn');
            const box = document.getElementById('indexStatusBox');
            if (!btn || !box) return;
            btn.disabled = true;

            let totalIndexed = 0;
            let totalSkipped = 0;
            let offset = 0;
            const limit = 5;
            box.style.display = 'block';

            try {
                box.innerHTML = '⏳ Checking configuration...';
                const conf = await fetchAPI('/store/pos/visual-search-config');
                if (!conf || !conf.configured) {
                    box.innerHTML = '❌ Visual search not configured on backend.<br><small>HF_TOKEN, PINECONE_API_KEY, and PINECONE_INDEX must be set in environment variables.</small>';
                    btn.disabled = false;
                    return;
                }

                while (true) {
                    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Fetching products… (${offset})`;
                    const pRes = await fetchAPI(`/store/pos/products-to-index?offset=${offset}&limit=${limit}`);
                    if (!pRes || !pRes.products) break;

                    const products = pRes.products;
                    if (products.length === 0) {
                        box.innerHTML = `✅ <strong>Indexing complete!</strong><br>📦 Indexed: ${totalIndexed} products<br>⏭️ Skipped: ${totalSkipped}`;
                        btn.innerHTML = '<i class="fas fa-check"></i> Index Complete';
                        break;
                    }

                    for (const p of products) {
                        box.innerHTML = `⏳ Processing Product #${p.id}...<br>Indexed: ${totalIndexed} / Skipped: ${totalSkipped}`;
                        if (!p.image || p.image.includes('placeholder')) {
                            totalSkipped++;
                            continue;
                        }

                        let imgUrl = p.image;
                        if (imgUrl.startsWith('/')) imgUrl = window.location.origin + imgUrl;

                        const result = await fetchAPI('/store/pos/index-product', {
                            method: 'POST',
                            body: JSON.stringify({ product_id: p.id, sku: p.sku, image_url: imgUrl })
                        });
                        if (result) totalIndexed++;
                    }
                    offset += limit;
                }
            } catch(e) {
                box.innerHTML = `❌ Network error: ${e.message}`;
                btn.innerHTML = '<i class="fas fa-sync-alt"></i> Resume Index';
            }
            btn.disabled = false;
        }

        // CUSTOMER INTELLIGENCE DATA LOADERS
        async function loadOverviewCharts() {
            const d = await fetchAPI('/store/analytics/kpis?period=' + currentPeriod);
            if (d) {
                createOrUpdateChart('pieChart', {
                    type: 'doughnut',
                    data: {
                        labels: ['New Users', 'Returning'],
                        datasets: [{
                            data: [d.new_customers?.value || 0, d.returning_customers?.value || 0],
                            backgroundColor: ['#3d8b5f', '#8b5cf6'],
                            borderWidth: 0,
                            hoverOffset: 8,
                        }]
                    },
                    options: {
                        cutout: '68%',
                        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${ctx.raw}` } } }
                    }
                });
            }

            const revData = await fetchAPI('/store/analytics/revenue?period=14d');
            if (revData && revData.daily) {
                const labels = revData.daily.map(x => x.date);
                const totals = revData.daily.map(x => x.orders);

                createOrUpdateChart('visitsChart', {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            { label: 'Orders', data: totals, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,.08)', borderWidth: 2.5, pointBackgroundColor: '#3b82f6', pointRadius: 4, fill: true, tension: 0.4 },
                        ]
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { display: true, position: 'top', labels: { font: { size: 11 }, usePointStyle: true, boxWidth: 8 } } },
                        scales: { x: { grid: { display: false }, ticks: { font: { size: 11 } } }, y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.04)' }, ticks: { precision: 0 } } }
                    }
                });
            }
        }

        async function loadKPIs(period) {
            const d = await fetchAPI('/store/analytics/kpis?period=' + (period || currentPeriod));
            if (!d) return;
            const setTxt = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
            
            setTxt('kpi-visits', fmtNum(d.visits?.value || 0));
            setTxt('kpi-visits-sub', `${fmtNum(d.product_views?.value || 0)} product views`);
            setTxt('kpi-new', fmtNum(d.new_customers?.value || 0));
            fmtChange(d.new_customers?.change, document.getElementById('kpi-new-change'));
            setTxt('kpi-returning', fmtNum(d.returning_customers?.value || 0));
            setTxt('kpi-ret-rate', `${d.repeat_rate?.value || 0}% repeat rate`);
            setTxt('kpi-revenue', 'ETB ' + fmtNum(d.revenue?.value || 0));
            fmtChange(d.revenue?.change, document.getElementById('kpi-revenue-change'));
            setTxt('kpi-orders', fmtNum(d.orders?.value || 0));
            fmtChange(d.orders?.change, document.getElementById('kpi-orders-change'));
            setTxt('kpi-aov', 'ETB ' + fmtNum(d.aov?.value || 0));
            fmtChange(d.aov?.change, document.getElementById('kpi-aov-change'));
            setTxt('kpi-clv', 'ETB ' + fmtNum(d.clv?.value || 0));
            setTxt('kpi-cart-adds', fmtNum(d.cart_adds?.value || 0));
            setTxt('kpi-abandon', (d.cart_abandonment_rate?.value || 0) + '%');
            setTxt('kpi-repeat', (d.repeat_rate?.value || 0) + '%');
            setTxt('kpi-total-customers', fmtNum(d.total_customers?.value || 0));
        }

        async function loadSalesData(period) {
            const labelEl = document.getElementById('salesPeriodLabel');
            if (labelEl) labelEl.textContent = 'Loading…';
            const d = await fetchAPI('/store/analytics/revenue?period=' + (period || currentPeriod));
            if (!d) return;
            if (labelEl) labelEl.textContent = `${d.daily.length} days`;

            const labels = d.daily.map(x => x.date);
            const revData = d.daily.map(x => x.revenue);
            const ordData = d.daily.map(x => x.orders);

            createOrUpdateChart('revenueChart', {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Revenue (ETB)', data: revData, backgroundColor: 'rgba(61,139,95,.75)', borderRadius: 6, yAxisID: 'y', order: 2 },
                        { label: 'Orders', data: ordData, type: 'line', borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 2.5, pointBackgroundColor: '#f59e0b', pointRadius: 4, tension: 0.4, yAxisID: 'y2', order: 1 }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'top', labels: { font: { size: 11 }, usePointStyle: true, boxWidth: 8 } } },
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.04)' }, position: 'left', ticks: { callback: v => 'ETB '+v.toLocaleString() } },
                        y2: { beginAtZero: true, grid: { display: false }, position: 'right', ticks: { precision: 0 } }
                    }
                }
            });

            createOrUpdateChart('catRevChart', {
                type: 'doughnut',
                data: { labels: d.by_category.map(x=>x.name), datasets: [{ data: d.by_category.map(x=>x.revenue), backgroundColor: COLORS, borderWidth: 2, borderColor: '#fff', hoverOffset: 8 }] },
                options: { cutout: '55%', plugins: { legend: { position: 'bottom', labels: { font: { size: 11 }, padding: 10, boxWidth: 12 } }, tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ETB ${Math.round(ctx.raw).toLocaleString()}` } } } }
            });

            createOrUpdateChart('prodRevChart', {
                type: 'bar',
                data: { labels: d.by_product.map(x=>x.name.length>20?x.name.slice(0,20)+'…':x.name), datasets: [{ label: 'Revenue (ETB)', data: d.by_product.map(x=>x.revenue), backgroundColor: COLORS, borderRadius: 6 }] },
                options: {
                    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { x: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.04)' }, ticks: { callback: v => 'ETB '+v.toLocaleString() } }, y: { grid: { display: false } } }
                }
            });

            const tbody = document.getElementById('salesTableBody');
            if (tbody) {
                tbody.innerHTML = d.daily.slice().reverse().map(x => `
                    <tr>
                        <td class="fw-600">${x.date}</td>
                        <td>ETB ${Math.round(x.revenue).toLocaleString()}</td>
                        <td>${x.orders}</td>
                    </tr>
                `).join('') || '<tr><td colspan="3" class="text-center text-muted py-3">No data for this period.</td></tr>';
            }
        }

        async function loadFunnelData(period) {
            const wrap = document.getElementById('funnelWrap');
            if (wrap) wrap.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>Loading…</div>';
            const d = await fetchAPI('/store/analytics/funnel?period=' + (period || currentPeriod));
            if (!d) return;

            const funnelColors = ['#3d8b5f','#3b82f6','#8b5cf6','#f59e0b','#14b8a6','#ec4899'];
            const maxCount = Math.max(...d.stages.map(s => s.count), 1);
            let html = '';
            d.stages.forEach((s, i) => {
                const w = Math.max(s.count / maxCount * 100, 2);
                html += `
                    <div class="funnel-stage">
                        <div class="funnel-label">${s.label}</div>
                        <div class="funnel-bar-outer">
                            <div class="funnel-bar-inner" style="width:${w}%;background:${funnelColors[i] || '#3d8b5f'}">
                                <span>${s.count.toLocaleString()}</span>
                            </div>
                        </div>
                        <div class="funnel-meta">
                            <div>${s.pct}%</div>
                            ${i > 0 ? `<div class="funnel-drop">↓ ${s.drop}% lost</div>` : ''}
                        </div>
                    </div>
                    ${i < d.stages.length - 1 ? '<div class="funnel-connector"><div class="funnel-connector-line"></div></div>' : ''}
                `;
            });
            if (wrap) wrap.innerHTML = html;

            createOrUpdateChart('funnelChart', {
                type: 'bar',
                data: {
                    labels: d.stages.map(s => s.label),
                    datasets: [{ label: 'Users', data: d.stages.map(s => s.count), backgroundColor: funnelColors, borderRadius: 8 }]
                },
                options: {
                    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { x: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.04)' } }, y: { grid: { display: false } } }
                }
            });
        }

        async function loadProductsData(period) {
            const tbody = document.getElementById('productsBody');
            if (tbody) tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4"><i class="fas fa-spinner fa-spin me-2"></i>Loading…</td></tr>';
            const d = await fetchAPI('/store/analytics/products?period=' + (period || currentPeriod));
            if (!d) return;

            const prods = d.products || [];
            if (tbody) {
                tbody.innerHTML = prods.map(p => `
                    <tr>
                        <td>
                            <div class="fw-600" style="max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${p.name}</div>
                            ${p.low_stock ? '<span style="background:#fee2e2;color:#991b1b;font-size:.65rem;padding:1px 6px;border-radius:20px;font-weight:700">⚠ Low Stock</span>' : ''}
                        </td>
                        <td><span class="${p.stock <= 0 ? 'tbl-badge-new' : ''}">${p.stock}</span></td>
                        <td>${p.views.toLocaleString()}</td>
                        <td>${p.cart_adds}</td>
                        <td class="fw-600">${p.orders}</td>
                        <td>${p.qty_sold}</td>
                        <td class="fw-600">ETB ${Math.round(p.revenue).toLocaleString()}</td>
                        <td><span style="background:${p.conversion>5?'#d1fae5':'#f1f5f9'};color:${p.conversion>5?'#065f46':'#475569'};padding:2px 8px;border-radius:20px;font-size:.76rem;font-weight:700">${p.conversion}%</span></td>
                        <td>${p.wishlist}</td>
                    </tr>
                `).join('') || '<tr><td colspan="9" class="text-center text-muted py-4">No product data for this period.</td></tr>';
            }

            const top8Views = [...prods].sort((a,b)=>b.views-a.views).slice(0,8);
            createOrUpdateChart('viewsChart', {
                type: 'bar',
                data: { labels: top8Views.map(p=>p.name.length>18?p.name.slice(0,18)+'…':p.name), datasets: [{ label: 'Views', data: top8Views.map(p=>p.views), backgroundColor: 'rgba(59,130,246,.75)', borderRadius: 6 }] },
                options: { indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{beginAtZero:true,grid:{color:'rgba(0,0,0,.04)'}},y:{grid:{display:false}}} }
            });

            const top8Rev = [...prods].sort((a,b)=>b.revenue-a.revenue).slice(0,8);
            createOrUpdateChart('topSalesChart', {
                type: 'bar',
                data: { labels: top8Rev.map(p=>p.name.length>18?p.name.slice(0,18)+'…':p.name), datasets: [{ label: 'Revenue (ETB)', data: top8Rev.map(p=>p.revenue), backgroundColor: COLORS.slice(0,8), borderRadius: 6 }] },
                options: { indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{beginAtZero:true,ticks:{callback:v=>'ETB '+v.toLocaleString()},grid:{color:'rgba(0,0,0,.04)'}},y:{grid:{display:false}}} }
            });
        }

        async function loadSegmentsData() {
            const d = await fetchAPI('/store/analytics/segments');
            if (!d) return;
            const segColors = {'VIP':'#f59e0b','High Value':'#8b5cf6','Frequent Buyer':'#3d8b5f','Returning':'#3b82f6','First Time Buyer':'#06b6d4','Window Shopper':'#94a3b8','Inactive':'#ef4444','Lost Customer':'#ec4899'};
            const segBadgeClass = {'VIP':'seg-vip','High Value':'seg-high','Frequent Buyer':'seg-freq','Returning':'seg-ret','First Time Buyer':'seg-first','Window Shopper':'seg-window','Inactive':'seg-inactive','Lost Customer':'seg-lost'};

            const segs = (d.segments || []).filter(s => s.count > 0);
            const tbody = document.getElementById('segmentBody');
            if (tbody) {
                tbody.innerHTML = (d.segments || []).map(s => `
                    <tr>
                        <td><span class="seg-badge ${segBadgeClass[s.segment]||'seg-window'}">${s.segment}</span></td>
                        <td class="fw-600">${s.count}</td>
                        <td>ETB ${s.revenue.toLocaleString()}</td>
                        <td>ETB ${s.avg_spend.toLocaleString()}</td>
                    </tr>
                `).join('');
            }

            createOrUpdateChart('segmentPieChart', {
                type: 'doughnut',
                data: { labels: segs.map(s=>s.segment), datasets: [{ data: segs.map(s=>s.count), backgroundColor: segs.map(s=>segColors[s.segment]||'#94a3b8'), borderWidth: 2, borderColor: '#fff', hoverOffset: 10 }] },
                options: { cutout:'55%', plugins:{ legend:{ position:'bottom', labels:{font:{size:11},padding:10,boxWidth:12} }, tooltip:{callbacks:{label:ctx=>` ${ctx.label}: ${ctx.raw} customers`}} } }
            });

            createOrUpdateChart('segmentRevChart', {
                type: 'bar',
                data: { labels: (d.segments||[]).map(s=>s.segment), datasets: [{ label: 'Revenue (ETB)', data: (d.segments||[]).map(s=>s.revenue), backgroundColor: (d.segments||[]).map(s=>segColors[s.segment]||'#94a3b8'), borderRadius: 8 }] },
                options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:v=>'ETB '+v.toLocaleString()},grid:{color:'rgba(0,0,0,.04)'}}} }
            });
        }

        async function loadCohortData() {
            const d = await fetchAPI('/store/analytics/cohort');
            if (!d) return;
            const cohorts = d.cohorts || [];
            const maxOffset = Math.max(...cohorts.map(c=>c.retention.length), 1);
            const headers = ['Cohort', 'Size', ...Array.from({length:maxOffset},(_,i)=>i===0?'M+0 (Join)':'M+'+i)];
            function heatColor(pct) {
                if (pct === 0) return 'background:#f8faff;color:#94a3b8';
                if (pct >= 60) return 'background:#d1fae5;color:#065f46';
                if (pct >= 30) return 'background:#fef3c7;color:#92400e';
                return 'background:#fee2e2;color:#991b1b';
            }
            let html = `<div class="table-responsive"><table class="cohort-table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>`;
            cohorts.forEach(c => {
                html += `<tr><td class="fw-600">${c.label}</td><td>${c.size}</td>`;
                for (let i=0; i<maxOffset; i++) {
                    const pct = c.retention[i] ?? 0;
                    html += `<td><span class="cohort-cell" style="${heatColor(pct)}">${pct > 0 ? pct+'%' : '—'}</span></td>`;
                }
                html += '</tr>';
            });
            html += '</tbody></table></div>';
            const wrap = document.getElementById('cohortWrap');
            if (wrap) wrap.innerHTML = html;
        }

        async function loadGeoData(period) {
            const wrap = document.getElementById('geoTableWrap');
            if (wrap) wrap.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>Loading…</div>';
            const d = await fetchAPI('/store/analytics/geographic?period=' + (period || currentPeriod));
            if (!d) return;

            const cities = d.cities || [];
            const maxOrders = Math.max(...cities.map(c=>c.orders), 1);
            let html = '<table class="table admin-table mb-0"><thead><tr><th>City</th><th>Orders</th><th>Revenue</th><th>Customers</th></tr></thead><tbody>';
            cities.forEach(c => {
                const pct = c.orders/maxOrders*100;
                html += `<tr>
                    <td class="fw-600">${c.city}</td>
                    <td>
                        <div>${c.orders}</div>
                        <div class="geo-bar"><div class="geo-bar-fill" style="width:${pct}%"></div></div>
                    </td>
                    <td>ETB ${Math.round(c.revenue).toLocaleString()}</td>
                    <td>${c.customers}</td>
                </tr>`;
            });
            html += cities.length ? '' : '<tr><td colspan="4" class="text-center text-muted py-4">No geographic data yet.</td></tr>';
            html += '</tbody></table>';
            if (wrap) wrap.innerHTML = html;

            createOrUpdateChart('geoCityChart', {
                type: 'doughnut',
                data: { labels: cities.slice(0,8).map(c=>c.city), datasets: [{ data: cities.slice(0,8).map(c=>c.orders), backgroundColor: COLORS, borderWidth: 2, borderColor: '#fff', hoverOffset: 8 }] },
                options: { cutout:'50%', plugins:{ legend:{position:'bottom',labels:{font:{size:11},padding:10,boxWidth:12}} } }
            });

            createOrUpdateChart('geoRegionChart', {
                type: 'bar',
                data: { labels: (d.regions||[]).map(r=>r.region), datasets: [{ label: 'Revenue (ETB)', data: (d.regions||[]).map(r=>r.revenue), backgroundColor: 'rgba(20,184,166,.75)', borderRadius: 8 }] },
                options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{callback:v=>'ETB '+v.toLocaleString()},grid:{color:'rgba(0,0,0,.04)'}}} }
            });
        }

        async function loadInsightsData() {
            function renderInsightsList(items, type) {
                const icons = {info:'💡', warn:'⚠️', rec:'🚀'};
                const icon = icons[type];
                if (!items || !items.length) {
                    return `<div class="text-muted" style="font-size:.83rem;text-align:center;padding:20px 0">No ${type === 'warn' ? 'warnings' : type === 'rec' ? 'recommendations' : 'insights'} right now. 🎉</div>`;
                }
                return items.map(txt => `
                    <div class="insight-card ${type}">
                        <div class="insight-icon">${icon}</div>
                        <div>${txt}</div>
                    </div>
                `).join('');
            }

            const d = await fetchAPI('/store/analytics/insights');
            if (!d) return;
            const p1 = document.getElementById('insightsPanel');
            const p2 = document.getElementById('warningsPanel');
            const p3 = document.getElementById('recommendationsPanel');
            if (p1) p1.innerHTML = renderInsightsList(d.insights, 'info');
            if (p2) p2.innerHTML = renderInsightsList(d.warnings, 'warn');
            if (p3) p3.innerHTML = renderInsightsList(d.recommendations, 'rec');
        }

        async function loadCustomersData() {
            const data = await fetchAPI('/store/customers');
            if (!data) return;
            const tbody = document.getElementById('customersTableBody');
            if (!tbody) return;
            if (data.customers.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">No customers found</td></tr>';
                return;
            }
            tbody.innerHTML = data.customers.map(u => `
                <tr>
                    <td>
                        <div class="fw-600 tbl-name-cell">${u.name}</div>
                        <div style="font-size:.72rem;color:var(--muted)">ID #${u.id}</div>
                    </td>
                    <td>${u.loyalty_icon} ${u.loyalty_level}</td>
                    <td>${u.telegram ? '@' + u.telegram : (u.telegram_id || '—')}</td>
                    <td><span class="fw-600">${u.total_orders}</span></td>
                    <td style="font-size:.82rem">ETB ${u.total_spent.toLocaleString()}</td>
                    <td><span class="tbl-badge-tg">${u.last_purchase || 'Active'}</span></td>
                    <td style="font-size:.76rem;color:var(--muted)">${u.last_purchase || '—'}</td>
                    <td></td>
                </tr>
            `).join('');
        }
    </script>
</body>
</html>
'''

target_path = r'c:\Users\dawit\Desktop\Liyu Kids Mart\app\templates\mini_app\store_app.html'
with open(target_path, 'w', encoding='utf-8') as f:
    f.write(store_app_html)

print("Successfully written updated store_app.html!")
