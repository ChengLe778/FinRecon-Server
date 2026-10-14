/* 数据导入：上传 → 预览 → 确认入库 / 取消 */
(function (global) {
  const { API, U } = global;
  global.Pages = global.Pages || {};

  global.Pages.Import = {
    template: `
    <div>
      <div class="panel">
        <div class="panel-title">
          <span>上传文件</span>
          <span class="sub">支持农行 / 中行 / 建行流水，以及进项、销项发票表（.xls / .xlsx）</span>
        </div>

        <el-upload
          drag :auto-upload="false" :show-file-list="false"
          :on-change="onFileChange" accept=".xls,.xlsx">
          <div style="padding:14px 0">
            <div style="font-size:34px;color:#9ca3af">＋</div>
            <div style="font-size:13.5px;color:#4b5563">把银行流水或发票表拖到这里，或点击选择</div>
            <div style="font-size:12px;color:#9ca3af;margin-top:6px">
              系统会自动识别文件所属银行与格式
            </div>
          </div>
        </el-upload>

        <div v-if="file" class="toolbar" style="margin-top:14px">
          <el-tag type="info" size="small">{{ file.name }}</el-tag>
          <el-select v-model="parserType" placeholder="自动识别" size="small" style="width:190px" clearable>
            <el-option v-for="p in parsers" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
          <el-select v-model="direction" placeholder="发票方向（自动）" size="small"
                     style="width:170px" clearable>
            <el-option label="销项（我们开的）" value="销项" />
            <el-option label="进项（取得的）" value="进项" />
          </el-select>
          <el-button type="primary" size="small" :loading="parsing" @click="doParse">
            解析预览
          </el-button>
          <el-button size="small" @click="reset" v-if="preview">重置</el-button>
        </div>

        <div v-if="parsing" class="empty-tip">解析中，大文件可能需要十几秒……</div>
      </div>

      <!-- 预览结果 -->
      <div class="panel" v-if="preview">
        <div class="panel-title">
          <span>解析预览</span>
          <span class="sub">{{ preview.filename }} · 识别为「{{ preview.parser_label }}」</span>
        </div>

        <el-alert :closable="false" type="info" style="margin-bottom:12px">
          <template #title>
            <div style="font-size:12.5px;line-height:1.7">
              <div>列映射说明：{{ preview.column_doc }}</div>
            </div>
          </template>
        </el-alert>

        <el-descriptions :column="5" border size="small" style="margin-bottom:12px">
          <el-descriptions-item label="解析行数">{{ preview.total_rows }}</el-descriptions-item>
          <el-descriptions-item label="将新增">
            <span class="c-blue" style="font-weight:600">{{ preview.new_rows }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="重复（库中已有）">
            <span :class="preview.dup_in_db ? 'c-amber' : ''">{{ preview.dup_in_db }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="文件内重复">{{ preview.dup_in_file }}</el-descriptions-item>
          <el-descriptions-item label="无效行跳过">{{ preview.skipped_rows }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="preview.skipped_sample && preview.skipped_sample.length" class="hint" style="margin-bottom:10px">
          跳过的行（如合计行、金额为 0 的行）：
          <span v-for="(s,i) in preview.skipped_sample" :key="i">
            第{{ s.row }}行({{ s.reason }})&nbsp;
          </span>
        </div>

        <el-table :data="preview.preview" size="small" border height="330" stripe>
          <el-table-column type="index" width="52" label="行" />
          <el-table-column v-for="col in previewCols" :key="col"
                           :prop="col" :label="col" min-width="120" show-overflow-tooltip />
        </el-table>
        <div class="hint" style="margin-top:8px">仅显示前 50 行，确认无误后再入库。</div>

        <div class="toolbar" style="margin-top:14px">
          <el-button type="primary" :loading="committing" @click="doCommit">
            确认入库（新增 {{ preview.new_rows }} 条）
          </el-button>
          <el-button @click="doCancel">取消，不入库</el-button>
        </div>
      </div>

      <!-- 导入历史 -->
      <div class="panel">
        <div class="panel-title">
          <span>导入历史</span>
          <el-button size="small" text @click="loadLogs">刷新</el-button>
        </div>
        <el-table :data="logs" size="small" border height="240">
          <el-table-column prop="imported_at" label="时间" width="150" />
          <el-table-column prop="filename" label="文件名" min-width="230" show-overflow-tooltip />
          <el-table-column prop="parser_type" label="类型" width="150" />
          <el-table-column prop="total_rows" label="解析" width="80" align="right" />
          <el-table-column prop="imported_rows" label="新增" width="80" align="right">
            <template #default="{row}"><span class="c-blue mono">{{ row.imported_rows }}</span></template>
          </el-table-column>
          <el-table-column prop="dup_rows" label="重复跳过" width="90" align="right">
            <template #default="{row}"><span class="c-amber mono">{{ row.dup_rows }}</span></template>
          </el-table-column>
          <el-table-column prop="operator" label="操作人" width="90" />
        </el-table>
      </div>
    </div>
    `,
    data() {
      return {
        U,
        file: null,
        parserType: '',
        direction: '',
        parsers: [],
        parsing: false,
        committing: false,
        preview: null,
        logs: [],
      };
    },
    computed: {
      previewCols() {
        return this.preview && this.preview.preview.length
          ? Object.keys(this.preview.preview[0]) : [];
      },
    },
    mounted() {
      this.loadLogs();
      API.parsers().then(d => { this.parsers = d; }).catch(() => {});
    },
    methods: {
      loadLogs() {
        API.importLogs().then(d => { this.logs = d; }).catch(() => {});
      },
      onFileChange(uploadFile) {
        this.file = uploadFile.raw || uploadFile;
        this.preview = null;
      },
      async doParse() {
        if (!this.file) return this.$message.warning('请先选择文件');
        this.parsing = true;
        try {
          this.preview = await API.parseUpload(this.file, this.parserType, this.direction);
          this.$message.success(`解析完成，共 ${this.preview.total_rows} 行`);
        } catch (e) {
          this.$message.error(U.err(e, '解析失败'));
        } finally {
          this.parsing = false;
        }
      },
      async doCommit() {
        this.committing = true;
        try {
          const r = await API.commitImport(this.preview.token);
          this.$message.success(`入库完成：新增 ${r.imported} 条，跳过重复 ${r.duplicate} 条`);
          this.preview = null;
          this.file = null;
          this.loadLogs();
          this.$emit('refresh');
        } catch (e) {
          this.$message.error(U.err(e, '入库失败'));
        } finally {
          this.committing = false;
        }
      },
      async doCancel() {
        if (this.preview) {
          await API.cancelImport(this.preview.token).catch(() => {});
        }
        this.preview = null;
        this.$message.info('已取消，未写入数据库');
      },
      reset() { this.preview = null; },
    },
  };
})(window);
