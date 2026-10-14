/* 应用入口：Vue 挂载 + Element Plus 注册 + hash 路由 */
(function (global) {
  const { createApp } = global.Vue;
  const { API, U, Pages } = global;

  /* 简洁线性图标（16px stroke） */
  const ICONS = {
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
    import: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    invoice: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    txn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
    counterparty: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    reconcile: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    report: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    logs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    expense: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10h18"/><path d="M7 15h1a3 3 0 0 1 0 6H7z"/><path d="M7 15V9a3 3 0 0 1 3-3h1"/><path d="M17 9a3 3 0 0 1-3-3V5"/><path d="M17 9h1a3 3 0 0 1 0 6h-1"/></svg>',
  };

  const MENUS = [
    { path: 'dashboard', label: '总览看板', icon: ICONS.dashboard, comp: 'Dashboard' },
    { path: 'reconcile', label: '对账工作台', icon: ICONS.reconcile, comp: 'Reconcile' },
    { path: 'invoice', label: '发票管理', icon: ICONS.invoice, comp: 'Invoice' },
    { path: 'txn', label: '银行流水', icon: ICONS.txn, comp: 'Txn' },
    { path: 'counterparty', label: '往来单位', icon: ICONS.counterparty, comp: 'Counterparty' },
    { path: 'report', label: '报表分析', icon: ICONS.report, comp: 'Report' },
    { path: 'expense', label: '费用分析', icon: ICONS.expense, comp: 'Expense' },
    { path: 'import', label: '数据导入', icon: ICONS.import, comp: 'Import' },
    { path: 'logs', label: '操作日志', icon: ICONS.logs, comp: 'Logs' },
  ];

  const app = createApp({
    data() {
      return {
        route: 'dashboard',
        dbType: 'sqlite',
        operator: localStorage.getItem('operator') || 'admin',
      };
    },

    computed: {
      menus() { return MENUS; },
      currentComp() {
        const m = MENUS.find(x => x.path === this.route) || MENUS[0];
        return Pages[m.comp] || { template: '<div class="empty-tip">页面加载失败</div>' };
      },
      currentTitle() {
        const m = MENUS.find(x => x.path === this.route) || MENUS[0];
        return m.label;
      },
      today() { return U.today(); },
      dbLabel() {
        return this.dbType === 'mysql' ? 'MySQL 数据库' : 'SQLite 数据库（内置）';
      },
    },

    methods: {
      go(path) {
        if (location.hash !== '#' + path) location.hash = '#' + path;
        else this.route = path;
      },
      onHash() {
        const h = (location.hash || '#dashboard').replace(/^#/, '');
        const valid = MENUS.some(m => m.path === h);
        this.route = valid ? h : 'dashboard';
      },
      async loadHealth() {
        try {
          const d = await API.health();
          const url = (d && d.database_url) || '';
          this.dbType = url.startsWith('mysql') ? 'mysql' : 'sqlite';
        } catch (e) { /* 服务不可达时保持默认 */ }
      },
    },

    mounted() {
      this.onHash();
      window.addEventListener('hashchange', this.onHash);
      this.loadHealth();
    },
  });

  app.use(global.ElementPlus, { locale: global.ElementPlusLocaleZhCn });
  /* 注册图标组件（按需使用） */
  if (global.ElementPlusIconsVue) {
    for (const [name, comp] of Object.entries(global.ElementPlusIconsVue)) {
      app.component(name, comp);
    }
  }
  app.mount('#app');
})(window);
