/* 操作日志：全量操作留痕查询 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  const TABLE_LABELS = {
    invoice: '发票', bank_txn: '银行流水', counterparty: '往来单位',
    counterparty_alias: '别名', reconciliation: '核销记录', import: '数据导入',
  };
  const ACTION_TAGS = {
    create: 'success', update: 'primary', delete: 'danger',
    import: 'warning', reverse: 'info', write_off: 'success',
  };

  global.Pages.Logs = {
    template: `
    <div>
      <div class="panel">
        <div class="panel-title">
          <span>操作日志 <span class="sub">所有增删改、导入、核销操作均留痕，不可篡改</span></span>
        </div>
        <div class="toolbar">
          <el-select v-model="q.table_name" placeholder="对象类型" clearable size="small" style="width:150px" @change="reload">
            <el-option v-for="t in meta.tables" :key="t" :label="tableLabel(t)" :value="t" />
          </el-select>
          <el-select v-model="q.action" placeholder="操作类型" clearable size="small" style="width:130px" @change="reload">
            <el-option v-for="a in meta.actions" :key="a" :label="a" :value="a" />
          </el-select>
          <el-select v-model="q.operator" placeholder="操作人" clearable filterable size="small" style="width:140px" @change="reload">
            <el-option v-for="o in meta.operators" :key="o" :label="o" :value="o" />
          </el-select>
          <el-input v-model="q.keyword" placeholder="内容关键字…" clearable size="small"
                    style="width:220px" @keyup.enter="reload" @clear="reload" />
          <el-button size="small" type="primary" @click="reload">查询</el-button>
          <el-button size="small" @click="reset">重置</el-button>
        </div>

        <el-table :data="rows" size="small" border stripe v-loading="loading">
          <el-table-column label="时间" width="160">
            <template #default="{row}">
              <span class="mono">{{ row.created_at }}</span>
            </template>
          </el-table-column>
          <el-table-column label="对象" width="110">
            <template #default="{row}">{{ tableLabel(row.table_name) }}</template>
          </el-table-column>
          <el-table-column label="记录" width="80" align="right">
            <template #default="{row}"><span class="mono">{{ row.record_id ?? '-' }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{row}">
              <el-tag size="small" :type="tagType(row.action)" effect="plain">{{ row.action }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作人" width="100" prop="operator" />
          <el-table-column label="详情" min-width="380">
            <template #default="{row}">
              <span class="hint mono" style="white-space:pre-wrap; word-break:break-all">{{ brief(row.diff) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination background layout="total, prev, pager, next, sizes"
                         :total="total" :current-page="q.page" :page-size="q.size"
                         :page-sizes="[20, 30, 50, 100]"
                         @current-change="p=>{q.page=p; load()}"
                         @size-change="s=>{q.size=s; q.page=1; load()}" />
        </div>
      </div>
    </div>
    `,

    data() {
      return {
        U,
        loading: false,
        rows: [],
        total: 0,
        q: { page: 1, size: 30, table_name: '', action: '', operator: '', keyword: '' },
        meta: { tables: [], actions: [], operators: [] },
      };
    },

    methods: {
      tableLabel(t) { return TABLE_LABELS[t] || t; },
      tagType(a) { return ACTION_TAGS[a] || 'info'; },
      brief(diff) {
        if (diff === null || diff === undefined) return '-';
        if (typeof diff === 'string') return diff;
        try { return JSON.stringify(diff); } catch (e) { return String(diff); }
      },
      async load() {
        this.loading = true;
        try {
          const d = await API.operationLogs(this.q);
          this.rows = d.items;
          this.total = d.total;
        } catch (e) {
          this.$message.error(U.err(e, '查询失败'));
        } finally {
          this.loading = false;
        }
      },
      async loadMeta() {
        try {
          this.meta = await API.logMeta();
        } catch (e) { /* 忽略 */ }
      },
      reload() { this.q.page = 1; this.load(); },
      reset() {
        this.q = { page: 1, size: 30, table_name: '', action: '', operator: '', keyword: '' };
        this.load();
      },
    },

    mounted() {
      this.loadMeta();
      this.load();
    },
  };
})(window);
