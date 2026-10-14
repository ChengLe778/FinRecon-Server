/* 对账工作台：自动匹配 / 候选勾选 / 手工指定 / 撤销 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Reconcile = {
    template: `
    <div>
      <!-- 概览 -->
      <div class="grid-2">
        <div class="stat-card">
          <div class="stat-label">应收（客户欠我）</div>
          <div class="stat-value c-red">{{ U.money(sum.ar.invoice_remaining) }}</div>
          <div class="stat-sub">
            开票 {{ U.money(sum.ar.invoice_total) }} · 已核销 {{ U.money(sum.ar.invoice_reconciled) }} ·
            待核销发票 {{ sum.ar.open_invoice_count }} 张 / 待核销收款 {{ sum.ar.open_txn_count }} 笔
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">应付（我欠供应商）</div>
          <div class="stat-value c-green">{{ U.money(sum.ap.invoice_remaining) }}</div>
          <div class="stat-sub">
            开票 {{ U.money(sum.ap.invoice_total) }} · 已核销 {{ U.money(sum.ap.invoice_reconciled) }} ·
            待核销发票 {{ sum.ap.open_invoice_count }} 张 / 待核销付款 {{ sum.ap.open_txn_count }} 笔
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">
          <span>对账工作台</span>
          <div>
            <el-checkbox v-model="autoApply" size="small" style="margin-right:12px">
              精确匹配直接核销
            </el-checkbox>
            <el-button size="small" type="primary" :loading="running" @click="run">
              重新对账
            </el-button>
          </div>
        </div>

        <el-tabs v-model="tab" @tab-change="onTab">
          <el-tab-pane label="应收对账（销项发票 × 收款流水）" name="ar" />
          <el-tab-pane label="应付对账（进项发票 × 付款流水）" name="ap" />
        </el-tabs>

        <div v-if="!result" class="empty-tip">
          点击「重新对账」开始匹配。系统会先找金额精确相等的组合，再推荐金额近似 / 部分付款 / 一笔冲多票的候选。
        </div>

        <template v-else>
          <!-- 精确匹配 -->
          <div class="panel-title" style="margin-top:6px">
            <span>① 金额精确匹配 <el-tag size="small" type="success">{{ autoList.length }} 条</el-tag></span>
            <span class="sub">同一往来单位、未核销金额完全相等，可直接核销</span>
          </div>
          <el-table :data="autoList" size="small" border stripe
                    height="230" @selection-change="s=>selAuto=s" ref="autoTable">
            <el-table-column type="selection" width="46" />
            <el-table-column label="往来单位" min-width="170" show-overflow-tooltip>
              <template #default="{row}">{{ row.detail.cp_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="发票" min-width="190">
              <template #default="{row}">
                {{ row.detail.invoice_no || '(无号码)' }}
                <span class="hint"> {{ row.detail.invoice_date }}</span>
                <div class="hint">未核销 {{ U.money(row.detail.invoice_remaining || row.amount) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="流水" min-width="190">
              <template #default="{row}">
                {{ row.detail.txn_date }} · {{ row.detail.bank }}
                <div class="hint">{{ row.detail.memo || '' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="核销金额" width="130" align="right">
              <template #default="{row}"><span class="mono c-blue"><b>{{ U.money(row.amount) }}</b></span></template>
            </el-table-column>
            <el-table-column prop="reason" label="依据" width="120" />
          </el-table>

          <!-- 候选 -->
          <div class="panel-title" style="margin-top:16px">
            <span>② 候选推荐 <el-tag size="small" type="warning">{{ candList.length }} 条</el-tag></span>
            <span class="sub">金额近似 / 部分付款 / 一笔冲多票 —— 勾选后由你确认</span>
          </div>
          <el-table :data="candList" size="small" border stripe
                    height="300" @selection-change="s=>selCand=s">
            <el-table-column type="selection" width="46" />
            <el-table-column label="类型" width="86" align="center">
              <template #default="{row}">
                <el-tag size="small" effect="plain"
                        :type="row.kind==='combo'?'warning':(row.kind==='approx'?'success':'info')">
                  {{ kindLabel(row.kind) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="往来单位" min-width="150" show-overflow-tooltip>
              <template #default="{row}">{{ row.detail.cp_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="发票" min-width="180">
              <template #default="{row}">
                <template v-if="row.kind==='combo'">
                  <el-tag v-for="n in row.detail.invoice_nos" :key="n" size="small" effect="plain"
                          style="margin-right:3px">{{ (n||'无号').slice(-8) }}</el-tag>
                  <div class="hint">合计 {{ U.money(row.detail.invoice_total) }}</div>
                </template>
                <template v-else>
                  {{ row.detail.invoice_no || '(无号码)' }}
                  <span class="hint"> {{ row.detail.invoice_date }}</span>
                  <div class="hint">未核销 {{ U.money(row.detail.invoice_remaining) }}</div>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="流水" min-width="170">
              <template #default="{row}">
                {{ row.detail.txn_date }} · {{ row.detail.bank }}
                <div class="hint">未核销 {{ U.money(row.detail.txn_remaining) }} {{ row.detail.memo || '' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="建议核销" width="130" align="right">
              <template #default="{row}"><span class="mono"><b>{{ U.money(row.amount) }}</b></span></template>
            </el-table-column>
            <el-table-column prop="reason" label="推荐理由" min-width="190" show-overflow-tooltip />
            <el-table-column label="可信度" width="90" align="center">
              <template #default="{row}">
                <el-tag size="small" :type="row.score>=90?'success':(row.score>=60?'warning':'info')">
                  {{ Math.round(row.score) }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>

          <!-- 差异池：名字一致但金额不一致 -->
          <div class="panel-title" style="margin-top:16px">
            <span>③ 金额不一致（差异池）
              <el-tag size="small" type="danger">{{ diffList.length }} 条</el-tag>
            </span>
            <span class="sub">单位对得上、金额对不上 —— 一律不自动核销，按建议人工确认</span>
          </div>
          <el-table :data="diffList" size="small" border stripe
                    height="280" @selection-change="s=>selDiff=s">
            <el-table-column type="selection" width="46" />
            <el-table-column label="差异类型" width="110" align="center">
              <template #default="{row}">
                <el-tag size="small" effect="plain"
                        :type="row.action==='near'?'warning':(row.action==='ignore'?'info':'danger')">
                  {{ row.action_label || row.kind }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="往来单位" min-width="150" show-overflow-tooltip>
              <template #default="{row}">{{ row.detail.cp_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="发票" min-width="170">
              <template #default="{row}">
                <template v-if="(row.invoice_ids||[]).length > 1">
                  <el-tag v-for="n in (row.detail.invoice_nos||[])" :key="n" size="small"
                          effect="plain" style="margin-right:3px">{{ (n||'无号').slice(-8) }}</el-tag>
                  <div class="hint">合计 {{ U.money(row.detail.invoice_total) }}</div>
                </template>
                <template v-else>
                  {{ row.detail.invoice_no || '(无号码)' }}
                  <span class="hint"> {{ row.detail.invoice_date }}</span>
                  <div class="hint">未核销 {{ U.money(row.detail.invoice_remaining) }}</div>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="流水" min-width="160">
              <template #default="{row}">
                {{ row.detail.txn_date }} · {{ row.detail.bank }}
                <div class="hint">未核销 {{ U.money(row.detail.txn_remaining) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="差额" width="120" align="right">
              <template #default="{row}">
                <span class="mono c-orange"><b>{{ U.money(row.diff_amount) }}</b></span>
              </template>
            </el-table-column>
            <el-table-column label="建议核销" width="120" align="right">
              <template #default="{row}"><span class="mono">{{ U.money(row.amount) }}</span></template>
            </el-table-column>
            <el-table-column prop="reason" label="说明" min-width="180" show-overflow-tooltip />
          </el-table>

          <div class="toolbar" style="margin-top:14px">
            <el-button type="primary" :disabled="!selAuto.length && !selCand.length"
                       :loading="confirming" @click="confirmSel">
              核销选中的 {{ selAuto.length + selCand.length }} 条
            </el-button>
            <el-button type="warning" :disabled="!selDiff.length"
                       :loading="confirming" @click="confirmDiff">
              按建议处理 {{ selDiff.length }} 条差异
            </el-button>
            <el-button @click="openManual">手工指定核销</el-button>
            <span class="hint" style="margin-left:8px">
              差异处理会把「差额」单独记账（抹零/手续费/汇损），不影响流水侧真实金额
            </span>
          </div>
        </template>
      </div>

      <!-- 核销记录 -->
      <div class="panel">
        <div class="panel-title">
          <span>核销记录</span>
          <div>
            <el-select v-model="recQ.status" size="small" style="width:110px" @change="loadRecords">
              <el-option label="全部" value="" />
              <el-option label="有效" value="active" />
              <el-option label="已撤销" value="reversed" />
            </el-select>
            <el-button size="small" style="margin-left:8px" @click="exportRec">导出</el-button>
          </div>
        </div>
        <el-table :data="records" size="small" border stripe height="330" v-loading="recLoading">
          <el-table-column label="发票" min-width="180">
            <template #default="{row}">
              {{ row.invoice_no || '(无号码)' }} · {{ row.invoice_date }}
              <div class="hint">{{ row.invoice_counterparty || '' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="流水" min-width="170">
            <template #default="{row}">
              {{ row.txn_date }} · {{ row.txn_bank }}
              <div class="hint">{{ row.txn_counterparty || '' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="核销金额" width="120" align="right">
            <template #default="{row}">
              <span class="mono" :class="row.status==='active'?'c-blue':'c-gray'">
                {{ U.money(row.amount) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="方式" width="70" align="center">
            <template #default="{row}">
              <el-tag size="small" effect="plain">{{ row.method==='auto'?'自动':'手工' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="86" align="center">
            <template #default="{row}">
              <el-tag size="small" :type="row.status==='active'?'success':'info'">
                {{ row.status==='active'?'有效':'已撤销' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_by" label="操作人" width="80" />
          <el-table-column prop="created_at" label="时间" width="130" />
          <el-table-column label="操作" width="80" align="center">
            <template #default="{row}">
              <el-button link type="danger" size="small" v-if="row.status==='active'"
                         @click="doReverse(row)">撤销</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-wrap">
          <el-pagination background layout="total, prev, pager, next" :total="recTotal"
                         :current-page="recQ.page" :page-size="recQ.size"
                         @current-change="p=>{recQ.page=p;loadRecords()}" />
        </div>
      </div>

      <!-- 单位对账工作台 -->
      <div class="panel">
        <div class="panel-title">
          <span>单位对账工作台</span>
          <div>
            <el-input v-model="wbQ.keyword" size="small" placeholder="往来单位" clearable
                      style="width:180px" @keyup.enter="loadWorkbench" />
            <el-checkbox v-model="wbQ.only_diff" size="small" style="margin:0 10px"
                         @change="loadWorkbench">只看有差额</el-checkbox>
            <el-button size="small" @click="loadWorkbench">查询</el-button>
          </div>
        </div>

        <el-descriptions :column="6" border size="small" style="margin-bottom:10px">
          <el-descriptions-item label="往来单位数">{{ wb.summary.counterparty_count }}</el-descriptions-item>
          <el-descriptions-item label="有差额单位">
            <b class="c-orange">{{ wb.summary.diff_unit_count }}</b>
          </el-descriptions-item>
          <el-descriptions-item label="发票金额">{{ U.money(wb.summary.invoice_total) }}</el-descriptions-item>
          <el-descriptions-item label="流水金额">{{ U.money(wb.summary.txn_total) }}</el-descriptions-item>
          <el-descriptions-item label="已核销">
            <span class="c-blue">{{ U.money(wb.summary.invoice_total - wb.summary.invoice_remaining) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="净差额">
            <b :class="U.num(wb.summary.gap)>0 ? (tab==='ar'?'c-red':'c-green') : 'c-blue'">
              {{ U.money(wb.summary.gap) }}
            </b>
          </el-descriptions-item>
        </el-descriptions>

        <el-table :data="wb.items" size="small" border stripe height="360" v-loading="wbLoading">
          <el-table-column label="往来单位" min-width="190" show-overflow-tooltip>
            <template #default="{row}">
              {{ row.counterparty }}
              <el-tag v-if="!row.counterparty_id" size="small" type="warning" effect="plain"
                      style="margin-left:4px">未认领</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="invoice_count" label="发票" width="70" align="right" />
          <el-table-column label="发票金额" width="125" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.invoice_total) }}</span></template>
          </el-table-column>
          <el-table-column prop="txn_count" label="流水" width="70" align="right" />
          <el-table-column label="流水金额" width="125" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.txn_total) }}</span></template>
          </el-table-column>
          <el-table-column label="已核销" width="125" align="right">
            <template #default="{row}"><span class="mono c-blue">{{ U.money(row.reconciled) }}</span></template>
          </el-table-column>
          <el-table-column label="发票未核销" width="125" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.invoice_remaining) }}</span></template>
          </el-table-column>
          <el-table-column label="流水未核销" width="125" align="right">
            <template #default="{row}"><span class="mono">{{ U.money(row.txn_remaining) }}</span></template>
          </el-table-column>
          <el-table-column label="差额" width="125" align="right">
            <template #default="{row}">
              <span class="mono" :class="gapClass(row)"><b>{{ U.money(row.gap) }}</b></span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{row}">
              <el-tag size="small" effect="plain"
                      :type="row.status==='已平'?'success':(row.status==='挂账'?'danger':(row.status==='多收/多付'?'warning':'info'))">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="openWbDetail(row)">对账明细</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-wrap">
          <el-pagination background layout="total, prev, pager, next" :total="wbTotal"
                         :current-page="wbQ.page" :page-size="wbQ.size"
                         @current-change="p=>{wbQ.page=p;loadWorkbench()}" />
        </div>
      </div>

      <!-- 单个单位对账明细 -->
      <el-drawer v-model="showWb" :title="'对账明细 · ' + wbName" size="72%">
        <el-descriptions :column="5" border size="small" style="margin-bottom:12px" v-if="wbDetail">
          <el-descriptions-item label="发票合计">{{ U.money(wbDetail.summary.invoice_total) }}</el-descriptions-item>
          <el-descriptions-item label="流水合计">{{ U.money(wbDetail.summary.txn_total) }}</el-descriptions-item>
          <el-descriptions-item label="已核销">
            <span class="c-blue">{{ U.money(wbDetail.summary.reconciled) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="已确认差额">
            <span class="c-orange">{{ U.money(wbDetail.summary.diff_confirmed) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="净差额">
            <b :class="U.num(wbDetail.summary.gap)>0 ? (tab==='ar'?'c-red':'c-green') : 'c-blue'">
              {{ U.money(wbDetail.summary.gap) }}
            </b>
          </el-descriptions-item>
        </el-descriptions>

        <el-tabs v-model="wbTab" v-if="wbDetail">
          <el-tab-pane :label="'发票 (' + wbDetail.invoices.length + ')'" name="inv">
            <el-table :data="wbDetail.invoices" size="small" border stripe height="480">
              <el-table-column prop="invoice_no" label="发票号码" width="170" show-overflow-tooltip />
              <el-table-column prop="invoice_date" label="开票日期" width="105" />
              <el-table-column prop="category" label="费用类别" width="100" />
              <el-table-column prop="item_names" label="内容" min-width="180" show-overflow-tooltip />
              <el-table-column label="价税合计" width="125" align="right">
                <template #default="{row}"><span class="mono">{{ U.money(row.total) }}</span></template>
              </el-table-column>
              <el-table-column label="未核销" width="125" align="right">
                <template #default="{row}">
                  <span class="mono" :class="U.num(row.remaining)>0?'c-red':'c-gray'">
                    <b>{{ U.money(row.remaining) }}</b>
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="'流水 (' + wbDetail.txns.length + ')'" name="txn">
            <el-table :data="wbDetail.txns" size="small" border stripe height="480">
              <el-table-column prop="txn_date" label="日期" width="105" />
              <el-table-column prop="bank" label="银行" width="90" />
              <el-table-column prop="memo" label="摘要" min-width="200" show-overflow-tooltip />
              <el-table-column prop="purpose" label="用途" min-width="160" show-overflow-tooltip />
              <el-table-column label="金额" width="125" align="right">
                <template #default="{row}"><span class="mono">{{ U.money(row.amount) }}</span></template>
              </el-table-column>
              <el-table-column label="未核销" width="125" align="right">
                <template #default="{row}">
                  <span class="mono" :class="U.num(row.remaining)>0?'c-green':'c-gray'">
                    <b>{{ U.money(row.remaining) }}</b>
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="'已核销 (' + wbDetail.pairs.length + ')'" name="pair">
            <el-table :data="wbDetail.pairs" size="small" border stripe height="480">
              <el-table-column prop="invoice_no" label="发票号码" width="170" show-overflow-tooltip />
              <el-table-column prop="txn_date" label="流水日期" width="105" />
              <el-table-column label="核销金额" width="125" align="right">
                <template #default="{row}"><span class="mono c-blue">{{ U.money(row.amount) }}</span></template>
              </el-table-column>
              <el-table-column label="差额" width="115" align="right">
                <template #default="{row}">
                  <span class="mono" :class="U.num(row.diff_amount)>0?'c-orange':'c-gray'">
                    {{ U.money(row.diff_amount) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="diff_reason" label="差额原因" min-width="140" />
              <el-table-column label="方式" width="80" align="center">
                <template #default="{row}">
                  <el-tag size="small" effect="plain">{{ row.method==='auto'?'自动':'手工' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="时间" width="130" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="'匹配建议 (' + wbSuggest.length + ')'" name="sug">
            <div class="hint" style="margin-bottom:8px">
              金额精确相等的会直接核销；金额不一致的只给建议，勾选后人工确认。
            </div>
            <el-table :data="wbSuggest" size="small" border stripe height="430"
                      @selection-change="s=>selWb=s">
              <el-table-column type="selection" width="46" />
              <el-table-column label="类型" width="100" align="center">
                <template #default="{row}">
                  <el-tag size="small" effect="plain" :type="row.action==='near'?'warning':'info'">
                    {{ row.action_label || (row.kind==='auto'?'精确':'候选') }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="发票" min-width="180">
                <template #default="{row}">
                  {{ row.detail.invoice_no || '(无号码)' }}
                  <div class="hint">未核销 {{ U.money(row.detail.invoice_remaining) }}</div>
                </template>
              </el-table-column>
              <el-table-column label="流水" min-width="170">
                <template #default="{row}">
                  {{ row.detail.txn_date }} · {{ row.detail.bank }}
                  <div class="hint">未核销 {{ U.money(row.detail.txn_remaining) }}</div>
                </template>
              </el-table-column>
              <el-table-column label="建议核销" width="120" align="right">
                <template #default="{row}"><span class="mono"><b>{{ U.money(row.amount) }}</b></span></template>
              </el-table-column>
              <el-table-column label="差额" width="110" align="right">
                <template #default="{row}">
                  <span class="mono c-orange">{{ U.money(row.diff_amount) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="reason" label="说明" min-width="170" show-overflow-tooltip />
            </el-table>
            <div class="toolbar" style="margin-top:10px">
              <el-button type="primary" size="small" :disabled="!selWb.length"
                         :loading="confirming" @click="confirmWb">
                按建议核销 {{ selWb.length }} 条
              </el-button>
              <el-button size="small" @click="openManual">手工指定核销</el-button>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-drawer>

      <!-- 手工指定 -->
      <el-dialog v-model="showManual" title="手工指定核销" width="720px">
        <div class="dialog-tip">
          系统没有推荐出合适组合时使用。选择一张发票和一笔流水，填写本次要核销的金额（不能超过两侧未核销余额）。
        </div>
        <el-form label-width="100px" size="small">
          <el-form-item label="发票">
            <el-select v-model="manual.invoice_id" filterable remote reserve-keyword
                       :remote-method="searchInvoice" :loading="searchingInv"
                       placeholder="输入发票号或往来单位搜索" style="width:100%">
              <el-option v-for="o in invOptions" :key="o.id"
                         :label="(o.invoice_no||'无号') + ' · ' + (o.counterparty_name||'') + ' · 未核销 ' + U.money(o.remaining)"
                         :value="o.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="流水">
            <el-select v-model="manual.txn_id" filterable remote reserve-keyword
                       :remote-method="searchTxn" :loading="searchingTxn"
                       placeholder="输入对方户名或摘要搜索" style="width:100%">
              <el-option v-for="o in txnOptions" :key="o.id"
                         :label="o.txn_date + ' · ' + (o.counterparty_name||'') + ' · ' + o.direction + ' ' + U.money(o.amount) + ' · 未核销 ' + U.money(o.remaining)"
                         :value="o.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="核销金额">
            <el-input v-model="manual.amount" placeholder="0.00" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="manual.remark" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button size="small" @click="showManual=false">取消</el-button>
          <el-button size="small" type="primary" :loading="confirming" @click="submitManual">确认核销</el-button>
        </template>
      </el-dialog>
    </div>
    `,
    data() {
      return {
        U,
        tab: 'ar',
        autoApply: true,
        running: false, confirming: false,
        result: null,
        selAuto: [], selCand: [], selDiff: [],
        sum: {
          ar: { invoice_total: 0, invoice_remaining: 0, invoice_reconciled: 0, open_invoice_count: 0, open_txn_count: 0 },
          ap: { invoice_total: 0, invoice_remaining: 0, invoice_reconciled: 0, open_invoice_count: 0, open_txn_count: 0 },
        },
        records: [], recTotal: 0, recLoading: false,
        recQ: { page: 1, size: 20, status: '' },
        showManual: false,
        manual: { invoice_id: null, txn_id: null, amount: '', remark: '' },
        invOptions: [], txnOptions: [], searchingInv: false, searchingTxn: false,
        // 单位对账工作台
        wbQ: { page: 1, size: 20, keyword: '', only_diff: false },
        wb: {
          items: [],
          summary: { counterparty_count: 0, diff_unit_count: 0, invoice_total: 0,
                     invoice_remaining: 0, txn_total: 0, txn_remaining: 0, gap: 0 },
        },
        wbTotal: 0, wbLoading: false,
        showWb: false, wbName: '', wbTab: 'inv', wbDetail: null, selWb: [], wbRow: null,
      };
    },
    computed: {
      autoList() { return this.result ? this.result[this.tab].auto : []; },
      candList() { return this.result ? this.result[this.tab].candidates : []; },
      diffList() { return this.result ? (this.result[this.tab].diffs || []) : []; },
      wbSuggest() {
        if (!this.wbDetail) return [];
        const s = this.wbDetail.suggestions || {};
        return [].concat(s.auto || [], s.diffs || []);
      },
    },
    mounted() {
      this.loadSummary(); this.loadRecords(); this.loadWorkbench();
    },
    methods: {
      kindLabel(k) {
        return { auto: '精确', approx: '近似', partial: '部分', over: '溢付', combo: '一冲多' }[k] || k;
      },
      gapClass(row) {
        const g = U.num(row.gap);
        if (Math.abs(g) < 0.005) return 'c-gray';
        if (g > 0) return this.tab === 'ar' ? 'c-red' : 'c-green';
        return 'c-blue';
      },

      /* ---------------- 单位对账工作台 ---------------- */
      async loadWorkbench() {
        this.wbLoading = true;
        try {
          const d = await API.workbench({
            scope: this.tab, keyword: this.wbQ.keyword,
            only_diff: this.wbQ.only_diff || undefined,
            page: this.wbQ.page, size: this.wbQ.size,
          });
          this.wb = d;
          this.wbTotal = d.total;
        } catch (e) {
          this.$message.error(U.err(e, '加载对账工作台失败'));
        } finally { this.wbLoading = false; }
      },
      async openWbDetail(row) {
        this.wbRow = row;
        this.wbName = row.counterparty;
        this.wbTab = 'inv';
        this.selWb = [];
        this.wbDetail = null;
        this.showWb = true;
        try {
          this.wbDetail = await API.workbenchDetail({
            scope: this.tab,
            counterparty_id: row.counterparty_id || undefined,
            name: row.counterparty_id ? undefined : row.counterparty,
          });
        } catch (e) {
          this.$message.error(U.err(e, '加载明细失败'));
        }
      },
      async confirmWb() {
        if (!this.selWb.length) return;
        this.confirming = true;
        try {
          const r = await API.confirmReconcile({
            pairs: this.selWb.map(p => ({
              invoice_ids: p.invoice_ids, txn_ids: p.txn_ids, amount: p.amount,
              diff_amount: p.diff_amount, diff_reason: p.action_label || p.reason,
            })),
            method: 'manual',
          });
          this.$message.success(`已生成 ${r.created} 条核销记录`);
          const row = this.wbRow;
          await this.openWbDetail(row);
          this.loadWorkbench(); this.loadSummary(); this.loadRecords();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '核销失败'));
        } finally { this.confirming = false; }
      },

      /* ---------------- 差异池处理 ---------------- */
      async confirmDiff() {
        if (!this.selDiff.length) return;
        let reason = '';
        try {
          const r = await this.$prompt(
            `将对 ${this.selDiff.length} 条差异按建议金额核销，差额单独记为「差异金额」。\n可填写差额原因（抹零 / 手续费 / 汇损 / 部分发货…）`,
            '差异处理确认',
            { inputPlaceholder: '差额原因（可留空）', inputValue: '金额差异' }
          );
          reason = r.value || '';
        } catch { return; }

        this.confirming = true;
        try {
          const res = await API.confirmReconcile({
            pairs: this.selDiff.map(p => ({
              invoice_ids: p.invoice_ids, txn_ids: p.txn_ids, amount: p.amount,
              diff_amount: p.diff_amount,
              diff_reason: reason || p.action_label || p.reason,
            })),
            method: 'manual',
          });
          this.$message.success(`已生成 ${res.created} 条核销记录，差额已单独记账`);
          this.selDiff = [];
          this.run();
          this.loadWorkbench(); this.loadSummary(); this.loadRecords();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '差异核销失败'));
        } finally { this.confirming = false; }
      },
      async loadSummary() {
        this.sum = await API.reconSummary().catch(() => this.sum);
      },
      async run() {
        this.running = true;
        try {
          const r = await API.runReconcile({ scope: this.tab, auto_apply: this.autoApply });
          this.result = r;
          const applied = r[this.tab].applied || 0;
          const d = r[this.tab].diffs ? r[this.tab].diffs.length : 0;
          this.$message.success(
            `对账完成：精确匹配 ${r[this.tab].auto.length} 条` +
            (applied ? `，已自动核销 ${applied} 条` : '') +
            `；金额不一致 ${d} 条进差异池；候选 ${r[this.tab].candidates.length} 条待确认`
          );
          if (applied) { this.loadSummary(); this.loadRecords(); this.loadWorkbench(); }
        } catch (e) {
          this.$message.error(U.err(e, '对账失败'));
        } finally { this.running = false; }
      },
      onTab() {
        this.result = null;
        this.selAuto = []; this.selCand = []; this.selDiff = [];
        this.wbQ.page = 1;
        this.loadWorkbench();
      },
      async confirmSel() {
        const pairs = [].concat(this.selAuto, this.selCand).map(p => ({
          invoice_ids: p.invoice_ids, txn_ids: p.txn_ids, amount: p.amount,
        }));
        if (!pairs.length) return;
        this.confirming = true;
        try {
          const r = await API.confirmReconcile({ pairs, method: 'manual' });
          this.$message.success(`已生成 ${r.created} 条核销记录`);
          this.run();
          this.loadSummary(); this.loadRecords();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '核销失败'));
        } finally { this.confirming = false; }
      },
      async loadRecords() {
        this.recLoading = true;
        try {
          const d = await API.reconRecords(this.recQ);
          this.records = d.items;
          this.recTotal = d.total;
        } finally { this.recLoading = false; }
      },
      async doReverse(row) {
        try {
          await this.$confirm(
            `撤销这笔核销？\n${U.money(row.amount)} 元（${row.invoice_no || '无号'} ← ${row.txn_date} ${row.txn_bank}）`,
            '撤销确认', { type: 'warning' }
          );
        } catch { return; }
        try {
          await API.reverseReconcile(row.id, '工作台撤销');
          this.$message.success('已撤销，两侧未核销金额已回滚');
          this.loadRecords(); this.loadSummary();
          if (this.result) this.run();
          this.$emit('refresh');
        } catch (e) { this.$message.error(U.err(e, '撤销失败')); }
      },
      exportRec() { API.exportReconcile({ fmt: 'xlsx', status: this.recQ.status }); },
      openManual() {
        this.manual = { invoice_id: null, txn_id: null, amount: '', remark: '' };
        this.invOptions = []; this.txnOptions = [];
        this.showManual = true;
      },
      async searchInvoice(kw) {
        if (!kw) return;
        this.searchingInv = true;
        try {
          const d = await API.invoices({
            keyword: kw, size: 20,
            direction: this.tab === 'ar' ? '销项' : '进项',
          });
          this.invOptions = d.items.filter(x => U.num(x.remaining) > 0);
        } finally { this.searchingInv = false; }
      },
      async searchTxn(kw) {
        if (!kw) return;
        this.searchingTxn = true;
        try {
          const d = await API.txns({
            keyword: kw, size: 20,
            direction: this.tab === 'ar' ? '收' : '支',
          });
          this.txnOptions = d.items.filter(x => U.num(x.remaining) > 0);
        } finally { this.searchingTxn = false; }
      },
      async submitManual() {
        if (!this.manual.invoice_id || !this.manual.txn_id) {
          return this.$message.warning('请同时选择发票和流水');
        }
        if (!parseFloat(this.manual.amount)) return this.$message.warning('请填写核销金额');
        this.confirming = true;
        try {
          const r = await API.confirmReconcile({
            pairs: [{
              invoice_ids: [this.manual.invoice_id],
              txn_ids: [this.manual.txn_id],
              amount: this.manual.amount,
            }],
            method: 'manual', remark: this.manual.remark,
          });
          this.$message.success(`已生成 ${r.created} 条核销记录`);
          this.showManual = false;
          this.loadRecords(); this.loadSummary();
          if (this.result) this.run();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '核销失败'));
        } finally { this.confirming = false; }
      },
    },
  };
})(window);
