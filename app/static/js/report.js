/* 报表：余额表（可下钻）/ 账龄分析 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Report = {
    template: `
    <div class="panel">
      <div class="panel-title">
        <span>报表中心</span>
        <div>
          <el-date-picker v-model="asOf" type="date" size="small" value-format="YYYY-MM-DD"
                          placeholder="账龄基准日（默认今天）" style="width:180px;margin-right:8px" />
          <el-button size="small" type="primary" @click="loadAll">刷新</el-button>
        </div>
      </div>

      <el-tabs v-model="tab">
        <!-- 余额表 -->
        <el-tab-pane label="应收 / 应付余额表" name="balance">
          <div class="toolbar" style="margin-top:6px">
            <el-radio-group v-model="scope" size="small" @change="loadBalance">
              <el-radio-button value="ar">应收（客户欠我）</el-radio-button>
              <el-radio-button value="ap">应付（我欠供应商）</el-radio-button>
            </el-radio-group>
            <el-input v-model="q.keyword" placeholder="往来单位" size="small"
                      style="width:190px" clearable @keyup.enter="loadBalance" />
            <el-date-picker v-model="dateRange" type="daterange" size="small"
                            value-format="YYYY-MM-DD" range-separator="至"
                            start-placeholder="开票起日" end-placeholder="开票止日"
                            style="width:240px" @change="onDate" />
            <el-button size="small" @click="loadBalance">查询</el-button>
            <div style="flex:1"></div>
            <el-button size="small" type="success" @click="expBalance('xlsx')">导出 Excel</el-button>
            <el-button size="small" @click="expBalance('csv')">导出 CSV</el-button>
          </div>

          <el-descriptions :column="4" border size="small" style="margin-bottom:12px">
            <el-descriptions-item label="往来单位">{{ bal.summary.counterparty_count }} 家</el-descriptions-item>
            <el-descriptions-item label="发票张数">{{ bal.summary.invoice_count }} 张</el-descriptions-item>
            <el-descriptions-item label="开票金额">{{ U.money(bal.summary.total) }}</el-descriptions-item>
            <el-descriptions-item label="未核销余额">
              <b :class="scope==='ar'?'c-red':'c-green'">{{ U.money(bal.summary.remaining) }}</b>
            </el-descriptions-item>
          </el-descriptions>

          <el-table :data="bal.items" size="small" border stripe height="440" v-loading="loading">
            <el-table-column type="index" width="46" label="#" />
            <el-table-column label="往来单位" min-width="220" show-overflow-tooltip>
              <template #default="{row}">
                <span>{{ row.counterparty }}</span>
                <el-tag v-if="!row.counterparty_id" size="small" type="warning" effect="plain"
                        style="margin-left:4px">未认领</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="invoice_count" label="发票张数" width="90" align="right" />
            <el-table-column label="开票金额" width="130" align="right">
              <template #default="{row}"><span class="mono">{{ U.money(row.total) }}</span></template>
            </el-table-column>
            <el-table-column label="已核销" width="130" align="right">
              <template #default="{row}"><span class="mono c-blue">{{ U.money(row.reconciled) }}</span></template>
            </el-table-column>
            <el-table-column label="未核销余额" width="140" align="right">
              <template #default="{row}">
                <span class="mono" :class="scope==='ar'?'c-red':'c-green'">
                  <b>{{ U.money(row.remaining) }}</b>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="最早 / 最晚开票" width="200">
              <template #default="{row}">
                <span class="hint">{{ row.earliest_date }} ~ {{ row.latest_date }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" align="center">
              <template #default="{row}">
                <el-button link type="primary" size="small" @click="drill(row)">查看明细</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- 账龄 -->
        <el-tab-pane label="账龄分析" name="aging">
          <div class="toolbar" style="margin-top:6px">
            <el-radio-group v-model="scope" size="small" @change="loadAging">
              <el-radio-button value="ar">应收账龄</el-radio-button>
              <el-radio-button value="ap">应付账龄</el-radio-button>
            </el-radio-group>
            <span class="hint">基准日 {{ aging.as_of }} · 按发票开票日期起算</span>
            <div style="flex:1"></div>
            <el-button size="small" type="success" @click="expAging('xlsx')">导出 Excel</el-button>
            <el-button size="small" @click="expAging('csv')">导出 CSV</el-button>
          </div>

          <el-descriptions :column="3" border size="small" style="margin-bottom:12px">
            <el-descriptions-item label="未核销合计">{{ U.money(aging.summary.total) }}</el-descriptions-item>
            <el-descriptions-item label="超 90 天">
              <b class="c-red">{{ U.money(aging.summary.overdue_90) }}</b>
            </el-descriptions-item>
            <el-descriptions-item label="涉及发票">{{ aging.summary.invoice_count }} 张</el-descriptions-item>
          </el-descriptions>

          <div ref="agingChart" class="chart" style="height:250px"></div>

          <div class="panel-title" style="margin-top:14px">
            <span>分往来单位账龄</span>
          </div>
          <el-table :data="aging.by_counterparty" size="small" border stripe height="360">
            <el-table-column label="往来单位" min-width="200" show-overflow-tooltip>
              <template #default="{row}">{{ row.counterparty }}</template>
            </el-table-column>
            <el-table-column v-for="b in bucketLabels" :key="b" :label="b" width="120" align="right">
              <template #default="{row}">
                <span class="mono" :class="U.num(row[b])>0 ? '' : 'c-gray'">{{ U.money(row[b]) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="合计" width="140" align="right">
              <template #default="{row}">
                <span class="mono c-red"><b>{{ U.money(row.total) }}</b></span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 下钻明细 -->
    <el-drawer v-model="showDrill" :title="'明细 · ' + drillName" size="58%">
      <el-descriptions :column="3" border size="small" style="margin-bottom:12px">
        <el-descriptions-item label="发票张数">{{ drillData.summary.count }}</el-descriptions-item>
        <el-descriptions-item label="开票金额">{{ U.money(drillData.summary.total) }}</el-descriptions-item>
        <el-descriptions-item label="未核销余额">
          <b class="c-red">{{ U.money(drillData.summary.remaining) }}</b>
        </el-descriptions-item>
      </el-descriptions>
      <el-table :data="drillData.items" size="small" border stripe>
        <el-table-column prop="invoice_no" label="发票号码" width="180" show-overflow-tooltip />
        <el-table-column prop="invoice_date" label="开票日期" width="110" />
        <el-table-column label="价税合计" width="130" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.total) }}</span></template>
        </el-table-column>
        <el-table-column label="已核销" width="130" align="right">
          <template #default="{row}"><span class="mono c-blue">{{ U.money(row.reconciled) }}</span></template>
        </el-table-column>
        <el-table-column label="未核销" width="130" align="right">
          <template #default="{row}">
            <span class="mono" :class="U.num(row.remaining)>0?'c-red':'c-gray'">
              <b>{{ U.money(row.remaining) }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="invoice_type" label="票种" min-width="150" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="110" />
      </el-table>
    </el-drawer>
    `,
    data() {
      return {
        U,
        tab: 'balance',
        scope: 'ar',
        asOf: '',
        loading: false,
        dateRange: null,
        q: { keyword: '', date_from: '', date_to: '' },
        bal: { items: [], summary: { counterparty_count: 0, invoice_count: 0, total: 0, remaining: 0 } },
        aging: { as_of: '', buckets: [], by_counterparty: [], summary: { total: 0, overdue_90: 0, invoice_count: 0 } },
        bucketLabels: [],
        showDrill: false, drillName: '',
        drillData: { items: [], summary: { count: 0, total: 0, remaining: 0 } },
      };
    },
    mounted() { this.loadAll(); },
    methods: {
      loadAll() { this.loadBalance(); this.loadAging(); },
      onDate(v) {
        this.q.date_from = v ? v[0] : '';
        this.q.date_to = v ? v[1] : '';
      },
      async loadBalance() {
        this.loading = true;
        try {
          this.bal = await API.balance(Object.assign({ scope: this.scope }, this.q));
        } catch (e) {
          this.$message.error(U.err(e, '加载余额表失败'));
        } finally { this.loading = false; }
      },
      async loadAging() {
        try {
          this.aging = await API.aging({ scope: this.scope, as_of: this.asOf });
          this.bucketLabels = this.aging.buckets.map(b => b.bucket);
          this.$nextTick(() => this.drawAging());
        } catch (e) {
          this.$message.error(U.err(e, '加载账龄失败'));
        }
      },
      drawAging() {
        const el = this.$refs.agingChart;
        if (!el) return;
        if (!this._chart) this._chart = echarts.init(el);
        const data = this.aging.buckets.filter(b => U.num(b.amount) > 0);
        this._chart.setOption({
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
                     valueFormatter: v => U.money(v) },
          grid: { left: 70, right: 24, top: 18, bottom: 34 },
          xAxis: { type: 'category', data: data.map(b => b.bucket + ' 天'), axisLabel: { fontSize: 11 } },
          yAxis: { type: 'value', axisLabel: { fontSize: 11, formatter: v => U.short(v) } },
          series: [{
            type: 'bar', barMaxWidth: 56,
            data: data.map(b => U.num(b.amount)),
            itemStyle: { color: p => ['#60a5fa','#34d399','#fbbf24','#fb923c','#f87171','#b91c1c'][p.dataIndex % 6] },
            label: { show: true, position: 'top', fontSize: 10, formatter: p => U.short(p.value) },
          }],
        });
      },
      async drill(row) {
        this.drillName = row.counterparty;
        this.showDrill = true;
        this.drillData = { items: [], summary: { count: 0, total: 0, remaining: 0 } };
        if (!row.counterparty_id) {
          this.$message.info('该往来单位尚未认领，暂无下钻明细（可在往来单位台账中归集后查看）');
          return;
        }
        try {
          this.drillData = await API.balance({
            scope: this.scope, drill: row.counterparty_id,
            date_from: this.q.date_from, date_to: this.q.date_to,
          });
        } catch (e) {
          this.$message.error(U.err(e, '加载明细失败'));
        }
      },
      expBalance(fmt) {
        API.exportBalance(Object.assign({ scope: this.scope, fmt, as_of: this.asOf }, this.q));
        this.$message.success('导出已开始，请查看浏览器下载');
      },
      expAging(fmt) {
        API.exportAging({ scope: this.scope, fmt, as_of: this.asOf });
        this.$message.success('导出已开始，请查看浏览器下载');
      },
    },
  };
})(window);
