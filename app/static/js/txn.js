/* 银行流水管理：列表 / 新增 / 编辑 / 删除（联动解除核销） */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Txn = {
    template: `
    <div class="panel">
      <div class="panel-title">
        <span>银行流水</span>
        <span class="sub">
          共 {{ total }} 笔 · 收款 <b class="c-red">{{ U.money(summary.income) }}</b> ·
          付款 <b class="c-green">{{ U.money(summary.expense) }}</b> ·
          净额 {{ U.money(summary.net) }}
        </span>
      </div>

      <div class="toolbar">
        <el-select v-model="q.bank" placeholder="全部银行" size="small" style="width:130px" clearable>
          <el-option v-for="b in banks" :key="b.bank" :label="b.bank + '(' + b.count + ')'" :value="b.bank" />
        </el-select>
        <el-select v-model="q.direction" placeholder="全部方向" size="small" style="width:110px" clearable>
          <el-option label="收款" value="收" />
          <el-option label="付款" value="支" />
        </el-select>
        <el-input v-model="q.keyword" placeholder="对方户名 / 摘要 / 用途" size="small"
                  style="width:210px" clearable @keyup.enter="load" />
        <el-date-picker v-model="dateRange" type="daterange" size="small"
                        value-format="YYYY-MM-DD" range-separator="至"
                        start-placeholder="交易起日" end-placeholder="交易止日"
                        style="width:250px" @change="onDate" />
        <el-select v-model="q.status" placeholder="核销状态" size="small" style="width:120px" clearable>
          <el-option label="未核销完" value="open" />
          <el-option label="已核销完" value="closed" />
        </el-select>
        <el-button size="small" type="primary" @click="load">查询</el-button>
        <el-button size="small" @click="resetQ">重置</el-button>
        <div style="flex:1"></div>
        <el-button size="small" type="success" @click="openAdd">＋ 补录流水</el-button>
        <el-dropdown @command="onExport" style="margin-left:8px">
          <el-button size="small">导出 <el-icon><arrow-down /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="xlsx">导出 Excel（当前筛选）</el-dropdown-item>
              <el-dropdown-item command="csv">导出 CSV（当前筛选）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <el-table :data="items" size="small" border stripe height="520" v-loading="loading">
        <el-table-column prop="txn_date" label="交易日期" width="106" />
        <el-table-column prop="bank" label="银行" width="100" />
        <el-table-column label="方向" width="70" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="row.direction==='收'?'danger':'success'">{{ row.direction }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="120" align="right">
          <template #default="{row}"><span class="mono"><b>{{ U.money(row.amount) }}</b></span></template>
        </el-table-column>
        <el-table-column label="已核销" width="110" align="right">
          <template #default="{row}">
            <span class="mono" :class="U.num(row.reconciled)>0?'c-blue':'c-gray'">{{ U.money(row.reconciled) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="未核销" width="120" align="right">
          <template #default="{row}">
            <span class="mono" :class="U.num(row.remaining)>0?'c-red':'c-gray'">
              <b>{{ U.money(row.remaining) }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="对方户名" min-width="200" show-overflow-tooltip>
          <template #default="{row}">
            <span>{{ row.counterparty_name_raw || '-' }}</span>
            <el-tag v-if="!row.counterparty_id" size="small" type="warning" effect="plain"
                    style="margin-left:4px">未认领</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="memo" label="摘要" width="120" show-overflow-tooltip />
        <el-table-column prop="purpose" label="用途" min-width="160" show-overflow-tooltip />
        <el-table-column label="来源" width="70" align="center">
          <template #default="{row}">
            <el-tag size="small" effect="plain" :type="row.source==='manual'?'warning':'info'">
              {{ row.source==='manual' ? '手工' : '导入' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right" align="center">
          <template #default="{row}">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="doDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination background layout="total, sizes, prev, pager, next"
                       :total="total" :current-page="q.page" :page-size="q.size"
                       :page-sizes="[20,50,100,200]"
                       @current-change="p=>{q.page=p;load()}"
                       @size-change="s=>{q.size=s;q.page=1;load()}" />
      </div>
    </div>

    <!-- 新增 / 编辑 -->
    <el-dialog v-model="showForm" :title="form.id ? '编辑流水' : '补录银行流水'" width="600px">
      <div class="dialog-tip" v-if="!form.id">
        用于补录银行导出文件中缺失的交易。金额请填正数，方向用「收 / 支」区分。
      </div>
      <el-form :model="form" label-width="110px" size="small">
        <el-form-item label="银行" required>
          <el-select v-model="form.bank" style="width:100%">
            <el-option v-for="b in banks" :key="b.bank" :label="b.bank" :value="b.bank" />
            <el-option label="农业银行" value="农业银行" />
            <el-option label="中国银行" value="中国银行" />
            <el-option label="建设银行" value="建设银行" />
          </el-select>
        </el-form-item>
        <el-form-item label="交易日期" required>
          <el-date-picker v-model="form.txn_date" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="方向" required>
          <el-radio-group v-model="form.direction">
            <el-radio value="收">收款（进账）</el-radio>
            <el-radio value="支">付款（出账）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="金额" required>
          <el-input v-model="form.amount" placeholder="正数" />
        </el-form-item>
        <el-form-item label="对方户名">
          <el-input v-model="form.counterparty_name" placeholder="客户 / 供应商名称" />
        </el-form-item>
        <el-form-item label="对方账号">
          <el-input v-model="form.counterparty_account" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="form.memo" />
        </el-form-item>
        <el-form-item label="用途">
          <el-input v-model="form.purpose" />
        </el-form-item>
        <el-form-item label="本方账号">
          <el-input v-model="form.account_no" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showForm=false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-drawer v-model="showDetail" :title="'流水详情 · ' + detail.txn_date" size="52%">
      <el-descriptions :column="2" border size="small" style="margin-bottom:14px">
        <el-descriptions-item label="银行">{{ detail.bank }}</el-descriptions-item>
        <el-descriptions-item label="方向">{{ detail.direction }}</el-descriptions-item>
        <el-descriptions-item label="金额">{{ U.money(detail.amount) }}</el-descriptions-item>
        <el-descriptions-item label="未核销">
          <b class="c-red">{{ U.money(detail.remaining) }}</b>
        </el-descriptions-item>
        <el-descriptions-item label="对方户名">{{ detail.counterparty_name_raw || '-' }}</el-descriptions-item>
        <el-descriptions-item label="对方账号">{{ detail.counterparty_account || '-' }}</el-descriptions-item>
        <el-descriptions-item label="摘要">{{ detail.memo || '-' }}</el-descriptions-item>
        <el-descriptions-item label="用途">{{ detail.purpose || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div class="panel-title">已核销到的发票（{{ (detail.reconciliations||[]).length }} 条）</div>
      <el-table :data="detail.reconciliations||[]" size="small" border>
        <el-table-column label="核销金额" width="120" align="right">
          <template #default="{row}"><span class="mono c-blue">{{ U.money(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column label="对应发票" min-width="230">
          <template #default="{row}">
            {{ row.invoice_no || '(无号码)' }} · {{ row.invoice_date }}
            <span class="mono">{{ U.money(row.invoice_total) }}</span>
            <div class="hint">{{ row.invoice_counterparty || '' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="row.status==='active'?'success':'info'">
              {{ row.status==='active'?'有效':'已撤销' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{row}">
            <el-button link type="danger" size="small" v-if="row.status==='active'"
                       @click="doReverse(row)">撤销</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
    `,
    data() {
      return {
        U,
        items: [], total: 0, loading: false,
        summary: { income: 0, expense: 0, net: 0 },
        banks: [], dateRange: null,
        q: { page: 1, size: 20, bank: '', direction: '', keyword: '', status: '', date_from: '', date_to: '' },
        showForm: false, saving: false,
        form: this.blankForm(),
        showDetail: false, detail: {},
      };
    },
    mounted() { this.loadBanks(); this.load(); },
    methods: {
      blankForm() {
        return {
          id: null, bank: '中国银行', txn_date: '', direction: '收',
          amount: '', counterparty_name: '', counterparty_account: '',
          memo: '', purpose: '', account_no: '',
        };
      },
      async loadBanks() {
        this.banks = await API.txnBanks().catch(() => []);
      },
      onDate(v) {
        this.q.date_from = v ? v[0] : '';
        this.q.date_to = v ? v[1] : '';
      },
      resetQ() {
        this.q = { page: 1, size: 20, bank: '', direction: '', keyword: '', status: '', date_from: '', date_to: '' };
        this.dateRange = null;
        this.load();
      },
      async load() {
        this.loading = true;
        try {
          const d = await API.txns(this.q);
          this.items = d.items;
          this.total = d.total;
          this.summary = d.summary;
        } catch (e) {
          this.$message.error(U.err(e, '加载失败'));
        } finally { this.loading = false; }
      },
      onExport(fmt) {
        API.exportTxns(Object.assign({}, this.q, { fmt, page: undefined, size: undefined }));
        this.$message.success('导出已开始，请查看浏览器下载');
      },
      openAdd() { this.form = this.blankForm(); this.showForm = true; },
      openEdit(row) {
        this.form = Object.assign(this.blankForm(), {
          id: row.id, bank: row.bank, txn_date: row.txn_date,
          direction: row.direction, amount: row.amount,
          counterparty_name: row.counterparty_name_raw,
          counterparty_account: row.counterparty_account,
          memo: row.memo, purpose: row.purpose, account_no: row.account_no,
        });
        this.showForm = true;
      },
      async save() {
        if (!this.form.txn_date) return this.$message.warning('请填写交易日期');
        if (!parseFloat(this.form.amount)) return this.$message.warning('金额不正确');
        this.saving = true;
        try {
          if (this.form.id) {
            await API.updateTxn(this.form.id, this.form);
            this.$message.success('已更新');
          } else {
            await API.createTxn(this.form);
            this.$message.success('已补录');
          }
          this.showForm = false;
          this.load(); this.loadBanks(); this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '保存失败'));
        } finally { this.saving = false; }
      },
      async doDelete(row) {
        try {
          await this.$confirm(
            `确定删除这笔流水？\n${row.txn_date} · ${row.bank} · ${row.direction} ${U.money(row.amount)} 元`,
            '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
          );
        } catch { return; }

        try {
          await API.deleteTxn(row.id, false);
          this.$message.success('已删除');
          this.load(); this.$emit('refresh');
        } catch (e) {
          if (e.code === 2) {
            try {
              await this.$confirm(
                `${e.message}\n\n将同时解除 ${e.data.recon_count} 条核销关系（合计 ${U.money(e.data.reconciled)} 元），` +
                `对应发票的已核销金额会自动回滚。此操作会写入操作日志。`,
                '该流水已参与核销', { type: 'warning', confirmButtonText: '确认删除并解除核销', cancelButtonText: '取消' }
              );
            } catch { return; }
            try {
              await API.deleteTxn(row.id, true);
              this.$message.success('已删除并解除核销关系');
              this.load(); this.$emit('refresh');
            } catch (e2) { this.$message.error(U.err(e2, '删除失败')); }
          } else {
            this.$message.error(U.err(e, '删除失败'));
          }
        }
      },
      async openDetail(row) {
        this.detail = await API.txn(row.id).catch(() => ({}));
        this.showDetail = true;
      },
      async doReverse(r) {
        try {
          await this.$confirm(`撤销这笔 ${U.money(r.amount)} 元的核销？`, '撤销确认', { type: 'warning' });
        } catch { return; }
        try {
          await API.reverseReconcile(r.id, '从流水详情撤销');
          this.$message.success('已撤销');
          this.detail = await API.txn(this.detail.id);
          this.load(); this.$emit('refresh');
        } catch (e) { this.$message.error(U.err(e, '撤销失败')); }
      },
    },
  };
})(window);
