/* 接口封装：统一处理 {code,msg,data} 响应体 */
(function (global) {
  const axios = global.axios;

  const http = axios.create({ baseURL: '', timeout: 300000 });
  http.interceptors.request.use(cfg => {
    cfg.headers['X-Operator'] = localStorage.getItem('operator') || 'admin';
    return cfg;
  });

  /** 解包响应：成功返回 data，失败抛出带 msg 的 Error */
  async function unwrap(promise) {
    const resp = await promise;
    const body = resp.data;
    if (!body || typeof body !== 'object' || !('code' in body)) return body;
    if (body.code !== 0) {
      const err = new Error(body.msg || '请求失败');
      err.code = body.code;
      err.data = body.data;
      throw err;
    }
    return body.data;
  }

  function download(url, params) {
    const qs = new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString();
    window.location.href = url + (qs ? '?' + qs : '');
  }

  global.API = {
    http,

    // 系统
    health: () => unwrap(http.get('/api/health')),

    // 导入
    parseUpload: (file, parserType, direction) => {
      const fd = new FormData();
      fd.append('file', file);
      if (parserType) fd.append('parser_type', parserType);
      if (direction) fd.append('direction', direction);
      return unwrap(http.post('/api/import/parse', fd, { timeout: 600000 }));
    },
    commitImport: token => unwrap(http.post('/api/import/commit', { token })),
    cancelImport: token => unwrap(http.post('/api/import/cancel', { token })),
    importLogs: () => unwrap(http.get('/api/import/logs')),
    parsers: () => unwrap(http.get('/api/import/parsers')),

    // 发票
    invoices: p => unwrap(http.get('/api/invoices', { params: p })),
    invoice: id => unwrap(http.get('/api/invoices/' + id)),
    createInvoice: d => unwrap(http.post('/api/invoices', d)),
    updateInvoice: (id, d) => unwrap(http.put('/api/invoices/' + id, d)),
    deleteInvoice: (id, force) => unwrap(http.delete('/api/invoices/' + id, { params: { force: !!force } })),

    // 流水
    txns: p => unwrap(http.get('/api/txns', { params: p })),
    txnBanks: () => unwrap(http.get('/api/txns/banks')),
    txn: id => unwrap(http.get('/api/txns/' + id)),
    createTxn: d => unwrap(http.post('/api/txns', d)),
    updateTxn: (id, d) => unwrap(http.put('/api/txns/' + id, d)),
    deleteTxn: (id, force) => unwrap(http.delete('/api/txns/' + id, { params: { force: !!force } })),

    // 往来单位
    counterparties: p => unwrap(http.get('/api/counterparties', { params: p })),
    unclaimed: () => unwrap(http.get('/api/counterparties/unclaimed')),
    claim: d => unwrap(http.post('/api/counterparties/claim', d)),
    autoCollect: d => unwrap(http.post('/api/counterparties/auto-collect', d || {})),
    createCounterparty: d => unwrap(http.post('/api/counterparties', d)),
    updateCounterparty: (id, d) => unwrap(http.put('/api/counterparties/' + id, d)),
    deleteCounterparty: id => unwrap(http.delete('/api/counterparties/' + id)),
    addAlias: (id, alias) => unwrap(http.post('/api/counterparties/' + id + '/aliases', { alias })),
    deleteAlias: (id, aid) => unwrap(http.delete('/api/counterparties/' + id + '/aliases/' + aid)),

    // 对账
    runReconcile: d => unwrap(http.post('/api/reconcile/run', d)),
    confirmReconcile: d => unwrap(http.post('/api/reconcile/confirm', d)),
    reverseReconcile: (id, reason) => unwrap(http.post('/api/reconcile/' + id + '/reverse', { reason })),
    reconRecords: p => unwrap(http.get('/api/reconcile/records', { params: p })),
    reconSummary: () => unwrap(http.get('/api/reconcile/summary')),
    workbench: p => unwrap(http.get('/api/reconcile/workbench', { params: p })),
    workbenchDetail: p => unwrap(http.get('/api/reconcile/workbench/detail', { params: p })),

    // 报表
    dashboard: () => unwrap(http.get('/api/reports/dashboard')),
    balance: p => unwrap(http.get('/api/reports/balance', { params: p })),
    aging: p => unwrap(http.get('/api/reports/aging', { params: p })),
    trend: p => unwrap(http.get('/api/reports/trend', { params: p })),
    top: p => unwrap(http.get('/api/reports/top', { params: p })),
    expense: p => unwrap(http.get('/api/reports/expense', { params: p })),

    // 导出
    exportBalance: p => download('/api/export/balance', p),
    exportAging: p => download('/api/export/aging', p),
    exportInvoices: p => download('/api/export/invoices', p),
    exportTxns: p => download('/api/export/txns', p),
    exportReconcile: p => download('/api/export/reconcile', p),
    exportExpense: p => download('/api/export/expense', p),

    // 日志
    operationLogs: p => unwrap(http.get('/api/logs/operations', { params: p })),
    logMeta: () => unwrap(http.get('/api/logs/meta')),
  };

  /* ---------------- 工具函数 ---------------- */
  global.U = {
    /** 12345.6 -> 12,345.60 */
    money(v, digits = 2) {
      if (v === null || v === undefined || v === '') return '-';
      const n = Number(v);
      if (isNaN(n)) return String(v);
      return n.toLocaleString('zh-CN', {
        minimumFractionDigits: digits, maximumFractionDigits: digits
      });
    },
    /** 简写：12,345.60 -> 1.23万 */
    short(v) {
      const n = Number(v);
      if (isNaN(n)) return '-';
      const a = Math.abs(n);
      if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿';
      if (a >= 1e4) return (n / 1e4).toFixed(2) + '万';
      return n.toFixed(2);
    },
    num(v) { return v === null || v === undefined || v === '' ? 0 : Number(v); },
    today() {
      const d = new Date();
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
             '-' + String(d.getDate()).padStart(2, '0');
    },
    err(e, fallback) {
      return (e && e.message) ? e.message : (fallback || '操作失败');
    },
  };
})(window);
