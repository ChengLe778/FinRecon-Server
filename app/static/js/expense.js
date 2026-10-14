/* 费用分析：餐饮 / 油费 / 住宿 / 材料 … 钱花在哪，一图看清 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  const PALETTE = ['#2563eb', '#f97316', '#10b981', '#8b5cf6', '#f43f5e', '#06b6d4',
                   '#eab308', '#6366f1', '#14b8a6', '#ec4899', '#84cc16', '#a855f7',
                   '#64748b', '#fb7185', '#38bdf8'];

  global.Pages.Expense = {
    template: `
    <div class="panel">
      <div class="panel-title">
        <span>费用结构分析</span>
        <div>
          <el-button size="small" type="success" @click="exp('xlsx')">导出 Excel</el-button>
          <el-button size="small" @click="exp('csv')">导出 CSV（明细）</el-button>
          <el-button size="small" type="primary" @click="load">刷新</el-button>
        </div>
      </div>

      <div class="toolbar" style="margin-top:6px">
        <el-radio-group v-model="direction" size="small" @change="load">
          <el-radio-button value="进项">进项（我付出去的）</el-radio-button>
          <el-radio-button value="销项">销项（我收进来的）</el-radio-button>
        </el-radio-group>
        <el-date-picker v-model="range" type="daterange" size="small"
                        value-format="YYYY-MM-DD" range-separator="至"
                        start-placeholder="开票起日" end-placeholder="开票止日"
                        style="width:240px" @change="onRange" />
        <el-select v-model="category" size="small" clearable placeholder="费用类别"
                   style="width:150px" @change="load">
          <el-option v-for="c in exp.categories" :key="c" :label="c" :value="c" />
        </el-select>
        <el-input v-model="keyword" size="small" clearable placeholder="名称 / 单位关键词"
                  style="width:200px" @keyup.enter="load" />
        <el-button size="small" @click="load">查询</el-button>
        <el-button size="small" link @click="reset">重置</el-button>
      </div>

      <el-alert v-if="U.num(exp.summary.uncategorized_invoice_count) > 0" type="warning"
                :closable="false" show-icon style="margin:8px 0"
                :title="'有 ' + exp.summary.uncategorized_invoice_count + ' 张发票没有明细行（旧数据未回填），未计入本表。可执行 scripts/backfill_invoice_items.py 回填。'" />

      <el-descriptions :column="6" border size="small" style="margin:10px 0">
        <el-descriptions-item label="价税合计">
          <b class="mono">{{ U.money(exp.summary.total) }}</b>
        </el-descriptions-item>
        <el-descriptions-item label="不含税金额">{{ U.money(exp.summary.amount) }}</el-descriptions-item>
        <el-descriptions-item label="税额">{{ U.money(exp.summary.tax) }}</el-descriptions-item>
        <el-descriptions-item label="类别数">{{ exp.summary.category_count }}</el-descriptions-item>
        <el-descriptions-item label="明细行">{{ exp.summary.item_count }}</el-descriptions-item>
        <el-descriptions-item label="涉及发票">{{ exp.summary.invoice_count }} 张</el-descriptions-item>
      </el-descriptions>

      <el-row :gutter="14">
        <el-col :span="10">
          <div class="panel-title"><span>类别占比</span></div>
          <div ref="pieChart" class="chart" style="height:320px"></div>
        </el-col>
        <el-col :span="14">
          <div class="panel-title"><span>月度趋势（按类别堆叠）</span></div>
          <div ref="lineChart" class="chart" style="height:320px"></div>
        </el-col>
      </el-row>

      <div class="panel-title" style="margin-top:12px">
        <span>类别明细（点击「查看」下钻到发票行）</span>
      </div>
      <el-table :data="exp.items" size="small" border stripe height="330" v-loading="loading"
                :summary-method="sumCat" show-summary>
        <el-table-column type="index" width="46" label="#" />
        <el-table-column label="费用类别" min-width="120">
          <template #default="{row}">
            <span class="dot" :style="{background: colorOf(row.category)}"></span>{{ row.category }}
          </template>
        </el-table-column>
        <el-table-column prop="invoice_count" label="发票张数" width="90" align="right" />
        <el-table-column prop="item_count" label="明细行" width="80" align="right" />
        <el-table-column label="不含税金额" width="130" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column label="税额" width="110" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.tax) }}</span></template>
        </el-table-column>
        <el-table-column label="价税合计" width="140" align="right">
          <template #default="{row}"><span class="mono"><b>{{ U.money(row.total) }}</b></span></template>
        </el-table-column>
        <el-table-column label="占比" width="130">
          <template #default="{row}">
            <el-progress :percentage="row.ratio" :stroke-width="12"
                         :color="colorOf(row.category)" text-inside />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{row}">
            <el-button link type="primary" size="small" @click="drill(row.category)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="panel-title" style="margin-top:14px">
        <span>TOP 20 往来单位（按费用类别拆分）</span>
      </div>
      <el-table :data="exp.suppliers" size="small" border stripe height="300">
        <el-table-column type="index" width="46" label="#" />
        <el-table-column prop="name" label="往来单位" min-width="200" show-overflow-tooltip />
        <el-table-column v-for="c in topCats" :key="c" :label="c" width="120" align="right">
          <template #default="{row}">
            <span class="mono" :class="U.num(row[c])>0 ? '' : 'c-gray'">{{ U.money(row[c] || 0) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="合计" width="140" align="right" fixed="right">
          <template #default="{row}"><span class="mono"><b>{{ U.money(row.total) }}</b></span></template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 下钻：某类别的发票明细行 -->
    <el-drawer v-model="showDrill" :title="'明细 · ' + drillCat" size="66%">
      <el-table :data="drillRows" size="small" border stripe height="calc(100vh - 180px)"
                v-loading="drillLoading">
        <el-table-column prop="invoice_date" label="开票日期" width="105" />
        <el-table-column prop="invoice_no" label="发票号码" width="170" show-overflow-tooltip />
        <el-table-column prop="seller" label="往来单位" min-width="180" show-overflow-tooltip />
        <el-table-column prop="name" label="货物或应税劳务名称" min-width="240" show-overflow-tooltip />
        <el-table-column prop="unit" label="单位" width="60" />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.qty, 2) }}</span></template>
        </el-table-column>
        <el-table-column label="不含税" width="115" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column prop="tax_rate" label="税率" width="70" />
        <el-table-column label="价税合计" width="125" align="right">
          <template #default="{row}"><span class="mono"><b>{{ U.money(row.total) }}</b></span></template>
        </el-table-column>
      </el-table>
      <div style="margin-top:10px;text-align:right">
        <el-pagination small background layout="total, prev, pager, next"
                       :total="drillTotal" :current-page="drillPage" :page-size="drillSize"
                       @current-change="p => { drillPage = p; loadDrill(); }" />
      </div>
    </el-drawer>
    `,

    data() {
      return {
        U,
        loading: false,
        direction: '进项',
        range: null,
        category: '',
        keyword: '',
        dateFrom: '',
        dateTo: '',
        exp: {
          items: [], months: [], suppliers: [], categories: [],
          summary: { total: 0, amount: 0, tax: 0, category_count: 0,
                     item_count: 0, invoice_count: 0, uncategorized_invoice_count: 0 },
        },
        showDrill: false, drillCat: '', drillRows: [], drillTotal: 0,
        drillPage: 1, drillSize: 20, drillLoading: false,
      };
    },

    computed: {
      topCats() {
        const set = {};
        (this.exp.suppliers || []).forEach(s => {
          Object.keys(s).forEach(k => { if (k !== 'name' && k !== 'total') set[k] = 1; });
        });
        return Object.keys(set).sort((a, b) => {
          const sa = (this.exp.suppliers || []).reduce((n, s) => n + U.num(s[a]), 0);
          const sb = (this.exp.suppliers || []).reduce((n, s) => n + U.num(s[b]), 0);
          return sb - sa;
        }).slice(0, 8);
      },
    },

    mounted() { this.load(); },

    methods: {
      params() {
        return {
          direction: this.direction,
          date_from: this.dateFrom,
          date_to: this.dateTo,
          category: this.category,
          keyword: this.keyword,
        };
      },
      onRange(v) {
        this.dateFrom = v ? v[0] : '';
        this.dateTo = v ? v[1] : '';
        this.load();
      },
      reset() {
        this.range = null; this.dateFrom = ''; this.dateTo = '';
        this.category = ''; this.keyword = '';
        this.load();
      },
      async load() {
        this.loading = true;
        try {
          this.exp = await API.expense(this.params());
          this.$nextTick(() => { this.drawPie(); this.drawLine(); });
        } catch (e) {
          this.$message.error(U.err(e, '加载费用分析失败'));
        } finally { this.loading = false; }
      },

      colorOf(cat) {
        const i = (this.exp.items || []).findIndex(x => x.category === cat);
        return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
      },

      drawPie() {
        const el = this.$refs.pieChart;
        if (!el) return;
        if (!this._pie) this._pie = echarts.init(el);
        const data = (this.exp.items || []).slice(0, 12).map(x => ({
          name: x.category, value: U.num(x.total),
        }));
        const rest = (this.exp.items || []).slice(12)
          .reduce((n, x) => n + U.num(x.total), 0);
        if (rest > 0) data.push({ name: '其他类别', value: rest });
        this._pie.setOption({
          tooltip: { trigger: 'item', formatter: p => `${p.name}<br/>${U.money(p.value)}（${p.percent}%）` },
          legend: { type: 'scroll', orient: 'vertical', right: 4, top: 10, itemWidth: 10,
                    itemHeight: 10, textStyle: { fontSize: 11 } },
          series: [{
            type: 'pie', radius: ['42%', '68%'], center: ['36%', '52%'],
            avoidLabelOverlap: true, data,
            itemStyle: { borderColor: '#fff', borderWidth: 2 },
            label: { show: false },
            emphasis: { label: { show: true, fontSize: 12, fontWeight: 'bold',
                                 formatter: '{b}\n{d}%' } },
          }],
        }, true);
      },

      drawLine() {
        const el = this.$refs.lineChart;
        if (!el) return;
        if (!this._line) this._line = echarts.init(el);
        const months = this.exp.months || [];
        const cats = (this.exp.items || []).slice(0, 8).map(x => x.category);
        const series = cats.map((c, i) => ({
          name: c, type: 'bar', stack: 'total', barMaxWidth: 40,
          emphasis: { focus: 'series' },
          itemStyle: { color: PALETTE[i % PALETTE.length] },
          data: months.map(m => U.num(m[c])),
        }));
        this._line.setOption({
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
                     valueFormatter: v => U.money(v) },
          legend: { type: 'scroll', top: 0, itemWidth: 10, itemHeight: 10,
                    textStyle: { fontSize: 11 } },
          grid: { left: 66, right: 16, top: 40, bottom: 30 },
          xAxis: { type: 'category', data: months.map(m => m.month),
                   axisLabel: { fontSize: 11 } },
          yAxis: { type: 'value', axisLabel: { fontSize: 11, formatter: v => U.short(v) } },
          series,
        }, true);
      },

      sumCat({ columns }) {
        return columns.map((c, i) => {
          if (i === 0) return '合计';
          if (c.property === 'invoice_count') return '';
          if (c.property === 'item_count') return (this.exp.items || []).reduce((n, x) => n + U.num(x.item_count), 0);
          if (c.property === 'amount') return U.money(this.exp.summary.amount);
          if (c.property === 'tax') return U.money(this.exp.summary.tax);
          if (c.property === 'total') return U.money(this.exp.summary.total);
          return '';
        });
      },

      drill(cat) {
        this.drillCat = cat;
        this.drillPage = 1;
        this.showDrill = true;
        this.loadDrill();
      },
      async loadDrill() {
        this.drillLoading = true;
        try {
          const p = Object.assign({}, this.params(), {
            drill: this.drillCat, page: this.drillPage, size: this.drillSize,
          });
          const d = await API.expense(p);
          this.drillRows = d.items || [];
          this.drillTotal = d.total || 0;
        } catch (e) {
          this.$message.error(U.err(e, '加载明细失败'));
        } finally { this.drillLoading = false; }
      },

      exp(fmt) {
        const p = Object.assign({}, this.params(), { fmt });
        if (this.category) p.category = this.category;
        API.exportExpense(p);
        this.$message.success('导出已开始，请查看浏览器下载');
      },
    },
  };
})(window);
