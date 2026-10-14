/* 往来单位台账：主数据 + 别名管理 + 未认领归集 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Counterparty = {
    template: `
    <div>
      <!-- 未认领 -->
      <div class="panel" v-if="unclaimed.length">
        <div class="panel-title">
          <span>未认领的原始名称 <el-tag size="small" type="warning">{{ unclaimed.length }} 组</el-tag></span>
          <div>
            <el-button size="small" type="primary" :loading="collecting" @click="autoCollect">
              一键自动归集
            </el-button>
          </div>
        </div>
        <div class="hint" style="margin-bottom:10px">
          这些名称在流水 / 发票里出现过，但还没有归属到某个往来单位。归集后，系统就能按单位汇总应收应付并自动对账。
          名称不同但实为同一家的（如简称、错别字），请选中后合并到同一个单位。
        </div>

        <el-table :data="unclaimed" size="small" border stripe height="260"
                  @selection-change="s=>selUnclaimed=s">
          <el-table-column type="selection" width="46" />
          <el-table-column label="名称" min-width="220" show-overflow-tooltip>
            <template #default="{row}">
              {{ row.name }}
              <el-tag v-if="row.variants.length>1" size="small" effect="plain" style="margin-left:4px">
                +{{ row.variants.length - 1 }} 种写法
              </el-tag>
              <div class="hint" v-if="row.variants.length>1">{{ row.variants.join(' / ') }}</div>
            </template>
          </el-table-column>
          <el-table-column label="流水笔数" width="90" align="right">
            <template #default="{row}"><span class="mono">{{ row.txn_count }}</span></template>
          </el-table-column>
          <el-table-column label="流水金额" width="130" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.txn_amount) }}</span></template>
          </el-table-column>
          <el-table-column label="发票张数" width="90" align="right">
            <template #default="{row}"><span class="mono">{{ row.invoice_count }}</span></template>
          </el-table-column>
          <el-table-column label="发票金额" width="130" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.invoice_amount) }}</span></template>
          </el-table-column>
        </el-table>

        <div class="toolbar" style="margin-top:12px">
          <el-button type="primary" size="small" :disabled="!selUnclaimed.length"
                     @click="claimSelected(false)">
            归集选中的 {{ selUnclaimed.length }} 组（新建往来单位）
          </el-button>
          <el-select v-model="mergeTarget" filterable placeholder="或合并到已有单位…"
                     size="small" style="width:260px">
            <el-option v-for="c in list" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <el-button size="small" :disabled="!selUnclaimed.length || !mergeTarget"
                     @click="claimSelected(true)">合并到该单位</el-button>
        </div>
      </div>

      <!-- 台账 -->
      <div class="panel">
        <div class="panel-title">
          <span>往来单位台账</span>
          <div>
            <el-input v-model="q.keyword" placeholder="名称 / 税号" size="small"
                      style="width:180px;margin-right:8px" clearable @keyup.enter="load" />
            <el-select v-model="q.type" placeholder="全部类型" size="small"
                       style="width:120px;margin-right:8px" clearable @change="load">
              <el-option label="客户" value="客户" />
              <el-option label="供应商" value="供应商" />
              <el-option label="其他" value="其他" />
            </el-select>
            <el-button size="small" type="success" @click="openAdd">＋ 新增</el-button>
          </div>
        </div>

        <el-table :data="list" size="small" border stripe height="480" v-loading="loading">
          <el-table-column label="名称" min-width="200" show-overflow-tooltip>
            <template #default="{row}">
              <b>{{ row.name }}</b>
              <div class="hint" v-if="row.tax_id">税号 {{ row.tax_id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="80" align="center">
            <template #default="{row}">
              <el-tag size="small" :type="row.type==='客户'?'danger':(row.type==='供应商'?'success':'info')">
                {{ row.type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="应收余额" width="130" align="right">
            <template #default="{row}">
              <span class="mono" :class="U.num(row.ar_balance)>0?'c-red':'c-gray'">
                {{ U.money(row.ar_balance) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="应付余额" width="130" align="right">
            <template #default="{row}">
              <span class="mono" :class="U.num(row.ap_balance)>0?'c-green':'c-gray'">
                {{ U.money(row.ap_balance) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="发票 / 流水" width="110" align="center">
            <template #default="{row}">
              <span class="mono">{{ row.invoice_count }} / {{ row.txn_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="别名" min-width="160">
            <template #default="{row}">
              <div class="tag-gap">
                <el-tag v-for="a in row.aliases" :key="a.id" size="small" effect="plain" closable
                        @close="delAlias(row, a)">{{ a.alias }}</el-tag>
                <el-button link type="primary" size="small" @click="addAlias(row)">+ 别名</el-button>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" align="center" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="doDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 新增 / 编辑 -->
      <el-dialog v-model="showForm" :title="form.id ? '编辑往来单位' : '新增往来单位'" width="560px">
        <el-form :model="form" label-width="110px" size="small">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="类型">
            <el-radio-group v-model="form.type">
              <el-radio value="客户">客户</el-radio>
              <el-radio value="供应商">供应商</el-radio>
              <el-radio value="其他">其他</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="纳税人识别号">
            <el-input v-model="form.tax_id" />
          </el-form-item>
          <el-form-item label="联系人">
            <el-input v-model="form.contact" />
          </el-form-item>
          <el-form-item label="电话">
            <el-input v-model="form.phone" />
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
    </div>
    `,
    data() {
      return {
        U,
        list: [], unclaimed: [], loading: false, collecting: false,
        q: { keyword: '', type: '' },
        selUnclaimed: [], mergeTarget: null,
        showForm: false, saving: false,
        form: { id: null, name: '', type: '客户', tax_id: '', contact: '', phone: '', remark: '' },
      };
    },
    mounted() { this.load(); this.loadUnclaimed(); },
    methods: {
      async load() {
        this.loading = true;
        try { this.list = await API.counterparties(this.q); }
        finally { this.loading = false; }
      },
      async loadUnclaimed() {
        this.unclaimed = await API.unclaimed().catch(() => []);
      },
      async autoCollect() {
        this.collecting = true;
        try {
          const r = await API.autoCollect({ min_count: 2 });
          this.$message.success(
            `已新建 ${r.created} 个往来单位，归集流水 ${r.linked_txn} 笔、发票 ${r.linked_invoice} 张`
          );
          this.load(); this.loadUnclaimed();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '归集失败'));
        } finally { this.collecting = false; }
      },
      async claimSelected(merge) {
        const names = [];
        this.selUnclaimed.forEach(u => names.push(...u.variants));
        if (!names.length) return;
        try {
          const payload = merge
            ? { names, counterparty_id: this.mergeTarget }
            : { names, type: '其他' };
          const r = await API.claim(payload);
          this.$message.success(r.msg || '归集完成');
          this.mergeTarget = null;
          this.load(); this.loadUnclaimed();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '归集失败'));
        }
      },
      openAdd() {
        this.form = { id: null, name: '', type: '客户', tax_id: '', contact: '', phone: '', remark: '' };
        this.showForm = true;
      },
      openEdit(row) {
        this.form = Object.assign({}, row, { aliases: undefined });
        this.showForm = true;
      },
      async save() {
        if (!this.form.name || !this.form.name.trim()) {
          return this.$message.warning('请填写名称');
        }
        this.saving = true;
        try {
          if (this.form.id) {
            await API.updateCounterparty(this.form.id, this.form);
            this.$message.success('已更新');
          } else {
            await API.createCounterparty(this.form);
            this.$message.success('已新增');
          }
          this.showForm = false;
          this.load();
        } catch (e) {
          this.$message.error(U.err(e, '保存失败'));
        } finally { this.saving = false; }
      },
      async doDelete(row) {
        try {
          await this.$confirm(
            `删除往来单位「${row.name}」？\n关联的 ${row.invoice_count} 张发票、${row.txn_count} 笔流水会解除归属（数据保留，不会删除）。`,
            '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
          );
        } catch { return; }
        try {
          await API.deleteCounterparty(row.id);
          this.$message.success('已删除');
          this.load(); this.loadUnclaimed();
        } catch (e) { this.$message.error(U.err(e, '删除失败')); }
      },
      async addAlias(row) {
        try {
          const { value } = await this.$prompt(
            `为「${row.name}」添加别名（该名称出现在流水/发票中时会自动归到这个单位）`,
            '添加别名', { confirmButtonText: '添加', cancelButtonText: '取消' }
          );
          if (!value) return;
          await API.addAlias(row.id, value);
          this.$message.success('已添加');
          this.load();
        } catch (e) {
          if (e && e.message) this.$message.error(U.err(e, '添加失败'));
        }
      },
      async delAlias(row, a) {
        try {
          await API.deleteAlias(row.id, a.id);
          this.$message.success('已删除别名');
          this.load();
        } catch (e) { this.$message.error(U.err(e, '删除失败')); }
      },
    },
  };
})(window);
