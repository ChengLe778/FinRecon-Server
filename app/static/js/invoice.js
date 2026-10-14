/* 发票管理：列表 / 新增 / 编辑 / 删除（联动解除核销） */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Invoice = {
    template: `
    <div class="panel">
      <div class="panel-title">
        <span>发票管理</span>
        <span class="sub">
          共 {{ total }} 张 · 价税合计 {{ U.money(summary.total) }} · 未核销
          <b class="c-red">{{ U.money(summary.remaining) }}</b>
        </span>
      </div>

      <div class="toolbar">
        <el-select v-model="q.direction" placeholder="全部方向" size="small" style="width:120px" clearable>
          <el-option label="销项（应收）" value="销项" />
          <el-option label="进项（应付）" value="进项" />
        </el-select>
        <el-input v-model="q.keyword" placeholder="发票号 / 往来单位" size="small"
                  style="width:200px" clearable @keyup.enter="load" />
        <el-date-picker v-model="dateRange" type="daterange" size="small"
                        value-format="YYYY-MM-DD" range-separator="至"
                        start-placeholder="开票起日" end-placeholder="开票止日"
                        style="width:250px" @change="onDate" />
        <el-select v-model="q.status" placeholder="核销状态" size="small" style="width:130px" clearable>
          <el-option label="未核销完" value="open" />
          <el-option label="已核销完" value="closed" />
          <el-option label="有效票" value="valid" />
          <el-option label="已红冲/作废" value="invalid" />
        </el-select>
        <el-select v-model="q.source" placeholder="全部来源" size="small" style="width:110px" clearable>
          <el-option label="导入" value="import" />
          <el-option label="手工" value="manual" />
        </el-select>
        <el-button size="small" type="primary" @click="load">查询</el-button>
        <el-button size="small" @click="resetQ">重置</el-button>
        <div style="flex:1"></div>
        <el-button size="small" type="success" @click="openAdd">＋ 手工新增</el-button>
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
        <el-table-column label="方向" width="80" align="center">
          <template #default="{row}">
            <el-tag size="small" :type="row.direction==='销项'?'danger':'success'">{{ row.direction }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="invoice_no" label="发票号码" width="180" show-overflow-tooltip />
        <el-table-column prop="invoice_date" label="开票日期" width="106" />
        <el-table-column label="往来单位" min-width="180" show-overflow-tooltip>
          <template #default="{row}">
            <span>{{ row.counterparty_name || '-' }}</span>
            <el-tag v-if="!row.counterparty_id" size="small" type="warning" effect="plain"
                    style="margin-left:4px">未认领</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="110" align="right">
          <template #default="{row}"><span class="mono">{{ U.money(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column label="税额" width="100" align="right">
          <template #default="{row}"><span class="mono c-gray">{{ U.money(row.tax) }}</span></template>
        </el-table-column>
        <el-table-column label="价税合计" width="120" align="right">
          <template #default="{row}"><span class="mono"><b>{{ U.money(row.total) }}</b></span></template>
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
        <el-table-column label="状态" width="118">
          <template #default="{row}">
            <el-tag size="small" v-if="!row.effective" type="info">已红冲</el-tag>
            <el-tag size="small" v-else-if="row.is_negative" type="warning">红字</el-tag>
            <el-tag size="small" v-else-if="U.num(row.remaining)<=0" type="success">已结清</el-tag>
            <el-tag size="small" v-else-if="U.num(row.reconciled)>0" type="warning">部分核销</el-tag>
            <el-tag size="small" v-else type="danger">未核销</el-tag>
          </template>
        </el-table-column>
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
    <el-dialog v-model="showForm" :title="form.id ? '编辑发票' : '手工新增发票'" width="640px">
      <div class="dialog-tip" v-if="!form.id">
        手工录入用于补录未纳入 Excel 的发票。同一张发票（方向+发票号+开票日期+价税合计）不能重复录入。
      </div>
      <el-form :model="form" label-width="110px" size="small">
        <el-form-item label="方向" required>
          <el-radio-group v-model="form.direction">
            <el-radio value="销项">销项（我们开的 → 应收）</el-radio>
            <el-radio value="进项">进项（取得的 → 应付）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="发票号码">
          <el-input v-model="form.invoice_no" placeholder="数电发票号码" />
        </el-form-item>
        <el-form-item label="开票日期">
          <el-date-picker v-model="form.invoice_date" type="date" value-format="YYYY-MM-DD"
                          placeholder="选择日期" style="width:100%" />
        </el-form-item>
        <el-form-item label="销方名称">
          <el-input v-model="form.seller_name" :placeholder="form.direction==='销项'?'（默认本公司）':'供应商名称'" />
        </el-form-item>
        <el-form-item label="购方名称">
          <el-input v-model="form.buyer_name" :placeholder="form.direction==='销项'?'客户名称':'（默认本公司）'" />
        </el-form-item>
        <el-form-item label="金额（不含税）">
          <el-input v-model="form.amount" @input="calcTotal" />
        </el-form-item>
        <el-form-item label="税额">
          <el-input v-model="form.tax" @input="calcTotal" />
        </el-form-item>
        <el-form-item label="价税合计" required>
          <el-input v-model="form.total" />
        </el-form-item>
        <el-form-item label="发票状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option label="正常" value="正常" />
            <el-option label="已红冲-全额" value="已红冲-全额" />
            <el-option label="作废" value="作废" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showForm=false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-drawer v-model="showDetail" :title="'发票详情 · ' + (detail.invoice_no || '(无号码)')" size="52%">
      <el-descriptions :column="2" border size="small" style="margin-bottom:14px">
        <el-descriptions-item label="方向">{{ detail.direction }}</el-descriptions-item>
        <el-descriptions-item label="开票日期">{{ detail.invoice_date || '-' }}</el-descriptions-item>
        <el-descriptions-item label="销方">{{ detail.seller_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="购方">{{ detail.buyer_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="金额">{{ U.money(detail.amount) }}</el-descriptions-item>
        <el-descriptions-item label="税额">{{ U.money(detail.tax) }}</el-descriptions-item>
        <el-descriptions-item label="价税合计">{{ U.money(detail.total) }}</el-descriptions-item>
        <el-descriptions-item label="未核销">
          <b class="c-red">{{ U.money(detail.remaining) }}</b>
        </el-descriptions-item>
        <el-descriptions-item label="票种">{{ detail.invoice_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div class="panel-title">核销记录（{{ (detail.reconciliations||[]).length }} 条）</div>
      <el-table :data="detail.reconciliations||[]" size="small" border>
        <el-table-column label="核销金额" width="120" align="right">
          <template #default="{row}"><span class="mono c-blue">{{ U.money(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column label="对应流水" min-width="230">
          <template #default="{row}">
            {{ row.txn_date }} · {{ row.txn_bank }} · {{ row.txn_direction }}
            <span class="mono">{{ U.money(row.txn_amount) }}</span>
            <div class="hint">{{ row.txn_counterparty || '' }} {{ row.txn_memo || '' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="方式" width="70" align="center">
          <template #default="{row}">
            <el-tag size="small" effect="plain">{{ row.method==='auto'?'自动':'手工' }}</el-tag>
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
        summary: { total: 0, remaining: 0 },
        dateRange: null,
        q: { page: 1, size: 20, direction: '', keyword: '', status: '', source: '', date_from: '', date_to: '' },
        showForm: false, saving: false,
        form: this.blankForm(),
        showDetail: false, detail: {},
      };
    },
    mounted() { this.load(); },
    methods: {
      blankForm() {
        return {
          id: null, direction: '销项', invoice_no: '', invoice_date: '',
          seller_name: '', buyer_name: '', amount: '', tax: '', total: '',
          status: '正常', remark: '', counterparty_id: null,
        };
      },
      onDate(v) {
        this.q.date_from = v ? v[0] : '';
        this.q.date_to = v ? v[1] : '';
      },
      resetQ() {
        this.q = { page: 1, size: 20, direction: '', keyword: '', status: '', source: '', date_from: '', date_to: '' };
        this.dateRange = null;
        this.load();
      },
      async load() {
        this.loading = true;
        try {
          const d = await API.invoices(this.q);
          this.items = d.items;
          this.total = d.total;
          this.summary = d.summary;
        } catch (e) {
          this.$message.error(U.err(e, '加载失败'));
        } finally { this.loading = false; }
      },
      onExport(fmt) {
        API.exportInvoices(Object.assign({}, this.q, { fmt, page: undefined, size: undefined }));
        this.$message.success('导出已开始，请查看浏览器下载');
      },
      calcTotal() {
        const a = parseFloat(this.form.amount) || 0;
        const t = parseFloat(this.form.tax) || 0;
        if (!this.form.total || this._autoTotal !== false) {
          this.form.total = (a + t).toFixed(2);
        }
      },
      openAdd() {
        this.form = this.blankForm();
        this.showForm = true;
      },
      openEdit(row) {
        this.form = Object.assign(this.blankForm(), {
          id: row.id, direction: row.direction, invoice_no: row.invoice_no,
          invoice_date: row.invoice_date, seller_name: row.seller_name,
          buyer_name: row.buyer_name, amount: row.amount, tax: row.tax,
          total: row.total, status: row.status || '正常', remark: row.remark,
          counterparty_id: row.counterparty_id,
        });
        this._autoTotal = false;
        this.showForm = true;
      },
      async save() {
        if (!this.form.total || parseFloat(this.form.total) === 0) {
          return this.$message.warning('价税合计不能为 0');
        }
        this.saving = true;
        try {
          if (this.form.id) {
            await API.updateInvoice(this.form.id, this.form);
            this.$message.success('已更新');
          } else {
            await API.createInvoice(this.form);
            this.$message.success('已新增');
          }
          this.showForm = false;
          this.load();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '保存失败'));
        } finally { this.saving = false; }
      },
      async doDelete(row) {
        try {
          await this.$confirm(
            `确定删除发票「${row.invoice_no || row.counterparty_name || row.id}」？\n价税合计 ${U.money(row.total)} 元`,
            '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
          );
        } catch { return; }

        try {
          await API.deleteInvoice(row.id, false);
          this.$message.success('已删除');
          this.load(); this.$emit('refresh');
        } catch (e) {
          if (e.code === 2) {
            // 已参与核销，需要二次确认
            try {
              await this.$confirm(
                `${e.message}\n\n将同时解除 ${e.data.recon_count} 条核销关系（合计 ${U.money(e.data.reconciled)} 元），` +
                `对应流水的未核销金额会自动回滚。此操作会写入操作日志。`,
                '该发票已参与核销', { type: 'warning', confirmButtonText: '确认删除并解除核销', cancelButtonText: '取消' }
              );
            } catch { return; }
            try {
              await API.deleteInvoice(row.id, true);
              this.$message.success('已删除并解除核销关系');
              this.load(); this.$emit('refresh');
            } catch (e2) { this.$message.error(U.err(e2, '删除失败')); }
          } else {
            this.$message.error(U.err(e, '删除失败'));
          }
        }
      },
      async openDetail(row) {
        this.detail = await API.invoice(row.id).catch(() => ({}));
        this.showDetail = true;
      },
      async doReverse(r) {
        try {
          await this.$confirm(`撤销这笔 ${U.money(r.amount)} 元的核销？`, '撤销确认', { type: 'warning' });
        } catch { return; }
        try {
          await API.reverseReconcile(r.id, '从发票详情撤销');
          this.$message.success('已撤销');
          this.detail = await API.invoice(this.detail.id);
          this.load(); this.$emit('refresh');
        } catch (e) { this.$message.error(U.err(e, '撤销失败')); }
      },
    },
  };
})(window);
