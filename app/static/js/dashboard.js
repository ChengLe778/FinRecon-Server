/* 总览看板 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Dashboard = {
    template: `
    <div>
      <div class="grid-4">
        <div class="stat-card">
          <div class="stat-label">应收余额（客户欠我）</div>
          <div class="stat-value c-red">{{ U.short(d.ar.balance) }}</div>
          <div class="stat-sub">{{ d.ar.open_count }} 家 / {{ d.ar.invoice_count }} 张发票未结清</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">应付余额（我欠供应商）</div>
          <div class="stat-value c-green">{{ U.short(d.ap.balance) }}</div>
          <div class="stat-sub">{{ d.ap.open_count }} 家 / {{ d.ap.invoice_count }} 张发票未结清</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">本月收款</div>
          <div class="stat-value c-red sm">{{ U.money(d.cash.month_income, 0) }}</div>
          <div class="stat-sub">上月同期 {{ U.money(d.cash.last_month_income, 0) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">本月付款</div>
          <div class="stat-value c-green sm">{{ U.money(d.cash.month_expense, 0) }}</div>
          <div class="stat-sub">上月同期 {{ U.money(d.cash.last_month_expense, 0) }}</div>
        </div>
      </div>

      <div class="grid-2">
        <div class="panel">
          <div class="panel-title">
            <span>月度收付款趋势</span>
            <el-radio-group v-model="months" size="small" @change="loadTrend">
              <el-radio-button :value="12">12 个月</el-radio-button>
              <el-radio-button :value="24">24 个月</el-radio-button>
              <el-radio-button :value="0">全部</el-radio-button>
            </el-radio-group>
          </div>
          <div ref="trendChart" class="chart"></div>
        </div>

        <div class="panel">
          <div class="panel-title"><span>应收账龄分布</span></div>
          <div ref="agingChart" class="chart"></div>
        </div>
      </div>

      <div class="grid-2">
        <div class="panel">
          <div class="panel-title">
            <span>欠款 TOP 榜（应收）</span>
            <el-button size="small" text @click="$emit('go','/report')">查看完整报表</el-button>
          </div>
          <el-table :data="tops" size="small" height="290" v-loading="loading">
            <el-table-column type="index" width="46" label="#" />
            <el-table-column prop="counterparty" label="往来单位" min-width="170" show-overflow-tooltip />
            <el-table-column label="未核销余额" min-width="120" align="right">
              <template #default="{row}">
                <span class="mono c-red">{{ U.money(row.remaining) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="最长账龄" width="100" align="right">
              <template #default="{row}">
                <span class="mono">{{ row.oldest_days }} 天</span>
              </template>
            </el-table-column>
            <el-table-column label="风险" width="70" align="center">
              <template #default="{row}">
                <el-tag size="small" :type="row.risk==='高'?'danger':(row.risk==='中'?'warning':'success')">
                  {{ row.risk }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="panel">
          <div class="panel-title"><span>系统概览</span></div>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="发票总数">{{ d.counts.invoice }} 张</el-descriptions-item>
            <el-descriptions-item label="银行流水">{{ d.counts.txn }} 笔</el-descriptions-item>
            <el-descriptions-item label="往来单位">{{ d.counts.counterparty }} 家</el-descriptions-item>
            <el-descriptions-item label="有效核销记录">{{ d.counts.recon_active }} 条</el-descriptions-item>
            <el-descriptions-item label="未认领流水">
              <span :class="d.unclaimed.txn ? 'c-amber' : ''">{{ d.unclaimed.txn }} 笔</span>
              <el-button link type="primary" size="small" v-if="d.unclaimed.txn"
                         @click="$emit('go','/counterparty')">去归集</el-button>
            </el-descriptions-item>
            <el-descriptions-item label="未认领发票">
              <span :class="d.unclaimed.invoice ? 'c-amber' : ''">{{ d.unclaimed.invoice }} 张</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="panel-title" style="margin-top:16px"><span>应收 / 应付核销进度</span></div>
          <div style="margin-bottom:10px">
            <div class="stat-label">应收 已核销 {{ U.money(d.ar.reconciled,0) }} / {{ U.money(d.ar.total,0) }}</div>
            <el-progress :percentage="pct(d.ar)" :stroke-width="14" :color="'#d93025'" />
          </div>
          <div>
            <div class="stat-label">应付 已核销 {{ U.money(d.ap.reconciled,0) }} / {{ U.money(d.ap.total,0) }}</div>
            <el-progress :percentage="pct(d.ap)" :stroke-width="14" :color="'#0f9d58'" />
          </div>
        </div>
      </div>
    </div>
    `,
    emits: ['go'],
    data() {
      return {
        U,
        loading: false,
        months: 12,
        tops: [],
        aging: null,
        d: {
          ar: { balance: 0, total: 0, reconciled: 0, open_count: 0, invoice_count: 0 },
          ap: { balance: 0, total: 0, reconciled: 0, open_count: 0, invoice_count: 0 },
          cash: { month_income: 0, month_expense: 0, last_month_income: 0, last_month_expense: 0 },
          counts: { invoice: 0, txn: 0, counterparty: 0, recon_active: 0 },
          unclaimed: { txn: 0, invoice: 0 },
        },
        _charts: [],
      };
    },
    computed: {
      U() { return global.U; },
    },
    mounted() {
      this.loadAll();
      this._resize = () => this._charts.forEach(c => c && c.resize());
      window.addEventListener('resize', this._resize);
    },
    beforeUnmount() {
      window.removeEventListener('resize', this._resize);
      this._charts.forEach(c => c && c.dispose());
    },
    methods: {
      pct(o) {
        const t = U.num(o.total);
        return t > 0 ? Math.min(100, Math.round(U.num(o.reconciled) / t * 100)) : 0;
      },
      async loadAll() {
        this.loading = true;
        try {
          this.d = await API.dashboard();
          this.tops = (await API.top({ scope: 'ar', limit: 10 })).items;
          this.aging = await API.aging({ scope: 'ar' });
          await this.loadTrend();
          this.$nextTick(() => { this.drawTrend(); this.drawAging(); });
        } catch (e) {
          this.$message.error(U.err(e, '加载看板失败'));
        } finally {
          this.loading = false;
        }
      },
      async loadTrend() {
        this.trend = (await API.trend({ months: this.months })).items;
        this.$nextTick(() => this.drawTrend());
      },
      drawTrend() {
        const el = this.$refs.trendChart;
        if (!el || !this.trend) return;
        const c = echarts.init(el);
        this._charts[0] = c;
        c.setOption({
          tooltip: {
            trigger: 'axis',
            valueFormatter: v => U.money(v, 0),
          },
          legend: { data: ['收款', '付款', '净额'], bottom: 0, itemWidth: 14 },
          grid: { left: 62, right: 20, top: 20, bottom: 42 },
          xAxis: { type: 'category', data: this.trend.map(x => x.month), axisLabel: { fontSize: 11 } },
          yAxis: {
            type: 'value',
            axisLabel: { fontSize: 11, formatter: v => U.short(v) },
          },
          series: [
            { name: '收款', type: 'bar', data: this.trend.map(x => U.num(x.income)), itemStyle: { color: '#d93025' } },
            { name: '付款', type: 'bar', data: this.trend.map(x => U.num(x.expense)), itemStyle: { color: '#0f9d58' } },
            { name: '净额', type: 'line', smooth: true, data: this.trend.map(x => U.num(x.net)),
              itemStyle: { color: '#2563eb' }, lineStyle: { width: 2 } },
          ],
        });
      },
      drawAging() {
        const el = this.$refs.agingChart;
        if (!el || !this.aging) return;
        const buckets = this.aging.buckets.filter(b => U.num(b.amount) > 0);
        const c = echarts.init(el);
        this._charts[1] = c;
        c.setOption({
          tooltip: { trigger: 'item', formatter: '{b}<br/>{c} ({d}%)' },
          legend: { bottom: 0, itemWidth: 12, textStyle: { fontSize: 11 } },
          series: [{
            type: 'pie',
            radius: ['42%', '68%'],
            center: ['50%', '44%'],
            avoidLabelOverlap: true,
            itemStyle: { borderColor: '#fff', borderWidth: 2 },
            label: { fontSize: 11, formatter: '{b}\n{d}%' },
            data: buckets.map(b => ({ name: b.bucket + ' 天', value: U.num(b.amount) })),
            color: ['#60a5fa', '#34d399', '#fbbf24', '#fb923c', '#f87171', '#b91c1c'],
          }],
        });
      },
    },
  };
})(window);
