# 資料完整性矩陣(Integrity Matrix)

**照妖鏡先照自己**:這份表逐格列出「輸入通道 × 異常型態」的引擎政策——每一格要嘛有明確行為(接受/警語/剔除/拒審)且被測試釘死,要嘛誠實標「未防禦」並寫明理由。沒有「靜默」格。

原則(整站一致):**fail-closed、絕不靜默替換、絕不填 0**。缺值/重複/無效率的統一門檻 = **5%**(`MISSING_MAX_RATE`):低於門檻 → 剔除受影響列並以警語揭露;達到門檻 → 結構化拒審(不是裸例外)。分工(★R21b★):前端 `parseCSV`(`app.js`)只做 **≥5% 快速拒審**(fail-fast UX,與引擎同判準)與 `pw_missing_detected` 揭露;**<5% 缺值【保留缺值位】(NaN→JSON null)連同日期原樣送引擎**,剔列/敏感度試算/降級全由引擎 `analyze`(`engine/judge_web.py`)執行——修前前端 <5% 先剔列,引擎在瀏覽器路徑永遠看不到 NaN,R21 敏感度機制在公開站是死碼(R21 驗收官 A 的 MED)。引擎同時是文件化的 direct-API 契約,本地 Streamlit 版直呼。null 直通真值:`verify_missing` 情境7(挖最差 19/400 → payload 含 19 個 null 逐位對應)。

測試錨點:`engine/test_r17.py`、`engine/test_r19.py`、`engine/test_r21.py`、`engine/test_engine.py`、`engine/test_detect_convert.py`(pytest);`tools/verify_missing.node.js`、`tools/verify_integrity.node.js`、`tools/verify_align.node.js`(前端路徑 node 真值)。

## returns(單一報酬序列)

| 異常 | 政策 | 測試 |
|---|---|---|
| 缺值 NaN/null <5% | 整期剔除 + 警語 `missing_values_dropped`(dates 同步)。**R21 加敏感度 fail-closed**:被剔除期真實報酬不可驗證 → 以「缺值集中在極端虧損日」情境(觀測最差單期報酬補入;p5 實測打不破 0.95 支柱=規則形同虛設,故用 min)重算夏普/DSR 併入警語;跌破雜訊地板(0.60)或高信心地板(0.95,判決倚賴的 DSR 支柱)且原判決為 likely-real → 降級 inconclusive(`missing_sensitivity_downgrade`);其餘只揭露不降級 | `test_r17.py::test_missing_small_dropped_matches_hand_cleaned`;`test_r21.py::test_missing_scam_downgraded_not_likely_real`、`::test_missing_sensitivity_honest_strong_edge_survives`、`::test_no_missing_clean_path_unchanged`;`verify_missing` 情境2 |
| 缺值 ≥5% | 結構化拒審 `missing_values_reject` | `test_r17.py::test_missing_scam_rejected_returns_mode`;`verify_missing` 情境1 |
| ±inf | 視同缺值(`isfinite` 判準),走上兩格 | `test_r19.py::test_inf_treated_as_missing` |
| 非數字型別(字串等) | 結構化拒審 `invalid_values_type`(不裸例外);前端 `parseNumberCell` 先轉 NaN 走缺值通道 | `test_r19.py::test_invalid_values_type_structured_reject` |
| 空序列 | 結構化拒審 `returns_empty` | `test_engine.py::test_warnings_coded_paths`、`::test_empty_and_short_inputs` |
| 極端值 \|r\|≥10(=1000%) | 警語 `extreme_returns`(多為百分比誤當小數;不拒審——可能是真極端事件);cagr 冪運算 Overflow 已夾 inf 不裸崩 | `test_r19.py::test_extreme_returns_unit_error_warned`、`::test_zero_crossing_nav_survives_as_returns_without_crash` |
| 負值/零 | 合法(報酬本可負可零),不動 | —(定義即政策) |
| 值重複(無日期) | **未防禦**——合法報酬本可重複,無日期時「複製列」與「巧合同值」數學上不可分;有日期時由 dates 通道攔截 | 誠實聲明 |

## nav → returns 轉換(淨值通道)

| 異常 | 政策 | 測試 |
|---|---|---|
| 缺值/不可解析 | NaN 傳播(自身與下一期都 NaN),交缺值守衛 | `test_detect_convert.py::test_nan_propagates_fail_closed` |
| 前值 prev==0 | 報酬不可定義 → **NaN 傳播**(R19 必修5b;舊行為記 0=憑空捏造持平日稀釋波動),兩層(app.js `navToReturns` / engine `nav_to_returns`)同語意 | `test_detect_convert.py::test_zero_prev_gives_nan`;`test_r19.py::test_nav_zero_prev_propagates_nan` |
| 跨零/含 0 的「淨值」 | 偵測邊界(文件化):all-positive 不成立 → 權威偵測判 returns,不做 nav 轉換;巨量值由 `extreme_returns` 警語+Overflow 夾值接手 | `test_r19.py::test_zero_crossing_nav_survives_as_returns_without_crash` |

## matrix(多策略矩陣,逐欄)

| 異常 | 政策 | 測試 |
|---|---|---|
| 任一欄缺值 ≥5% | 拒審 `missing_values_reject`(帶欄名) | `test_r17.py::test_missing_matrix_column_reject_names_column`;`verify_missing` 情境3 |
| 各欄 <5% 但含缺格列合計 ≥5% | 拒審 `missing_rows_reject` | `test_r17.py::test_missing_matrix_union_rate_rejects`;`verify_missing` 情境5 |
| 缺格列合計 <5% | 整列剔除(橫斷面對齊)+ `missing_rows_dropped`。**R21 加贏家欄敏感度 fail-closed**(同 returns 格政策):被剔列中贏家欄自己有值者用真實值補(已知就用已知)、贏家欄自身缺值才補觀測 min;跌破 0.60/0.95 且原判 likely-real → 降級 | `test_r17.py::test_missing_matrix_rows_dropped_keeps_alignment`;`test_r21.py::test_missing_sensitivity_matrix_winner_uses_known_values`;`verify_missing` 情境4 |
| 欄名重複 | 前端:後欄改名 `名稱(2)` 保留 + `pw_dup_colname`(修前後欄**靜默覆蓋**前欄) | `verify_integrity` 情境7 |
| 欄名重複(引擎端) | **未防禦(結構上不可見)**——matrix 以 JSON 物件/Python dict 傳遞,鍵唯一性在解析層已強制(後鍵勝),引擎收到時重複已消失;防線只能在前端(已設) | 誠實聲明 |
| 非數字型別 | 拒審 `invalid_values_type` | `test_r19.py::test_invalid_values_type_structured_reject` |
| 全空欄(行尾逗號) | 前端剔欄 + `pw_empty_col_dropped`(有標題的全空欄則走缺值拒審) | `verify_missing` 情境6 |
| 各欄長度不一(direct API) | 接受、截到最短欄(既有文件化行為)、**無警語=未防禦**——前端整列解析天然等長,此格僅 direct-API 構造錯誤能踩到;直呼方請自行對齊 | 誠實聲明 |

## dates(日期欄)——R19 必修1 主戰場

| 異常 | 政策 | 測試 |
|---|---|---|
| 時戳重複 ≥5% | 結構化拒審 `duplicate_dates_reject`(訊息講明:重複列會複製好日子灌水;日內請帶完整時戳)。實證:年化夏普 -0.72 的真虧策略,複製最好 60 天×4 按日期排序 → 舊路徑判 86 分 likely-real | `test_r19.py::test_duplicate_dates_scam_rejected`、`::test_duplicate_dates_mild_still_rejected`、`::test_duplicate_scam_magnitude_evidence`(騙局規模佐證)、`::test_matrix_duplicate_dates_reject_and_drop`;`verify_integrity` 情境1/2 |
| 時戳重複 <5% | 保留首見、重複列整列剔除 + `duplicate_dates_dropped`(合成進 keep_mask,基準/turnover 同步) | `test_r19.py::test_duplicate_dates_small_dropped_keeps_first_seen`;`verify_integrity` 情境3 |
| 混格式同刻(`2023-01-16` vs `2023/1/16`) | 引擎以【解析後時間戳】判重(逐元素 `errors="coerce"`;整欄格式推斷把變體 coerce 成 NaT 時退 `format="mixed"` 逐元素再試,取 NaT 較少者——格式變體閃避不了) | `test_r19.py::test_duplicate_dates_mixed_format_same_instant_caught`、`::test_format_variant_duplicates_with_garbage_still_caught` |
| 唯一但非遞增(亂序) | 依日期**穩定排序**恢復真實時序 + `dates_sorted`(排序是恢復真相不是竄改;揭露亂序筆數);判決與事先排好序的同資料**逐位一致**;`benchmark_idx` 同步重映射 | `test_r19.py::test_unsorted_dates_sorted_matches_presorted`、`::test_unsorted_dates_benchmark_idx_remapped`;`verify_integrity` 情境4 |
| 日內完整時戳(唯一) | 不誤傷(唯一性用完整時戳) | `test_r19.py::test_intraday_unique_timestamps_not_hurt`;`verify_integrity` 情境5 |
| 日內 date-only(同日多 bar) | 撞成重複 → 拒審,訊息明示「請提供含時間的完整時間戳記」 | `test_r19.py::test_dateonly_intraday_rejected_with_timestamp_hint`;`verify_integrity` 情境5 |
| 部分不可解析(垃圾日期在場;**毒日期繞過已封,R19b**) | 逐元素 `errors="coerce"`:**可解析列照常做時戳級重複判定**(修前任一垃圾日期讓整個守衛 early-return——1 格 "n/a" 即可靜默解除重複守衛;分母=全列數、門檻照舊 5%);垃圾(NaT)列**不參與重複判定**(N/A 不是時戳,identical 垃圾字串不構成「複製好日子」證據,屬「日期壞掉」問題交 `ppy_fallback` 承擔);任一 NaT 在場 → **跳過時序排序步**(無法建立全序)+ `dates_partially_unparseable` 揭露(排序只在全可解析時發生,keep_mask/sort_perm 座標語意不變)。前端 `applyDateIntegrity` 完全同政策(`pw_dates_partial_unparseable`;修前 identical 垃圾字串被當重複時戳 → 值全有效+幾列 "N/A" 日期整檔誤殺拒審) | `test_r19.py::test_poison_date_cannot_disarm_duplicate_guard`、`::test_partial_garbage_dates_skip_sort_disclosed`、`::test_small_dup_with_garbage_dropped_and_disclosed`;`verify_integrity` 情境8 |
| 全部不可解析(全垃圾) | **行為等同無日期**(列級檢查不執行、不給新能力;無「部分解析」警語——沒有可解析列可言);年化回退 252 + `ppy_fallback` 警語 | `test_r19.py::test_unparseable_dates_skip_row_guard_keep_ppy_fallback`、`::test_all_garbage_dates_equivalent_to_no_dates`;`test_engine.py::test_ppy_fallback_warning_on_bad_dates` |
| 長度與 values 不符 | 警語 `dates_len_mismatch`(日期僅用於頻率推斷,列級檢查不執行;修前完全靜默) | `test_r19.py::test_dates_length_mismatch_warned` |
| **混時區時間戳**(+00:00 與 +08:00 混用,正當 ISO-8601) | **R21 必修3(R21b 補齊 pandas 版本分歧)**:pandas ≥3 預告行為=整批解析 raise「Mixed timezones detected」→ 修前 idx=None → 守衛【靜默解除】=混時區重複騙局繞過;pandas 1.x/2.x 現行行為=回 **object dtype** Timestamp 序列(無 NaT、不 raise)→ R21 只看 None/NaT 的重試閘不觸發,`astype("int64")` 對 Timestamp 物件裸炸 TypeError(公開站混時區 CSV 直接噴例外)。修後兩型都視為「未正確解析」→ `utc=True` 重試:統一 UTC 後照常判重/排序(同一瞬間的不同時區寫法正確撞成重複);utc 重試後仍 object dtype 的兜底 → 視同整批解析失敗走誠實跳過警語;ppy/跨度通道同樣 utc 重試(不再假 `ppy_fallback`) | `test_r21.py::test_mixed_timezone_duplicate_scam_caught`、`::test_mixed_timezone_clean_guard_runs_and_ppy_inferred`、`::test_mixed_timezone_small_dup_dropped` |
| **整批解析例外**(含 utc 重試皆失敗) | 守衛跳過 + **必發** `date_guard_skipped_unparseable` 警語(修前完全靜默;跳過不算通過)。註:與「全垃圾字串」(coerce 全 NaT=行為等同無日期,上格政策)不同——本格是解析層丟例外的防線 | `test_r21.py::test_date_guard_total_parse_failure_skips_with_warning` |
| 極端跨度(遠古/未來年份) | **未防禦(接受)**——ppy/跨度照資料推;短窗已有 `short_calendar_span` 警語,長窗無已知灌分路徑 | 誠實聲明 |

## benchmark_returns(對照基準)——R19 必修3 主戰場

| 異常 | 政策 | 測試 |
|---|---|---|
| NaN/inf ≥5%(FWER 端) | 誠實降級 `zero_fallback`(coverage=有限值比例)+ `fwer_bench_nonfinite_fallback_zero`(修前 `nan_to_num` 填 0 **稀釋基準**=反保守 fail-open) | `test_r19.py::test_fwer_bench_nonfinite_over_threshold_degrades` |
| NaN/inf <5%(FWER 端) | 該些列從 FWER 剔除(mat+bench 同步)+ `fwer_bench_nonfinite_rows_dropped`(FWER 樣本數與主判決不同,照實揭露);與手動清理版逐位一致 | `test_r19.py::test_fwer_bench_nonfinite_small_dropped_equals_hand` |
| NaN/inf(基準比較卡) | 配對子集內 **pairwise 剔除**非有限對,`n_paired` 如實反映 + `bench_pairs_dropped_nonfinite` | `test_r19.py::test_bench_cmp_nonfinite_pairwise_dropped_equals_hand` |
| NaN/inf(基準曲線) | 視覺層維持 `nan_to_num` 平接(不進判分);缺值已由上兩格警語揭露 | 政策文件化(本表+程式註解) |
| 非數字型別 | 警語 `bench_invalid_type` + 略過所有基準通道 | `test_r19.py::test_bench_turnover_invalid_type_skipped_with_warning` |
| 長度不符(引擎剔列後) | 三態:同步剔除(aligned)/ 無法重配對 → `zero_fallback` + 警語 / 無 idx 位置退路也吃 keep_mask+sort_perm(R19 必修2,修錯位配對) | `test_r17.py::test_fwer_bench_cost_curve_remap_after_row_drop`、`::test_fwer_bench_unpairable_after_row_drop_degrades_honestly`;`test_r19.py::test_bench_fallback_no_idx_syncs_keep_mask` |
| 覆蓋率不足(前端對齊) | 交集 <80% → 誠實略過比較(不填 0 灌長度) | `verify_align.node.js` |
| 亂序+無 idx+基準短於策略(direct API 極窄組合) | **未防禦(概略對照)**——位置退路無法在重排後保證對應,維持既有「概略對照」語意;前端路徑(有日期交集 idx)不受影響 | 誠實聲明 |

## benchmark_idx(配對索引)

| 異常 | 政策 | 測試 |
|---|---|---|
| 長度/範圍不符 | 判無效 → 警語 `bench_pair_idx_invalid` + 位置配對退路 | `test_r17.py::test_bench_pair_idx_invalid_falls_back_with_warning` |
| **型別異常**(字串元素/非可迭代/非整數浮點) | **R21 必修4**:修前 `['a']*50` → ValueError 裸崩、`7` → TypeError 裸崩、非整數浮點被 `astype(int)` 靜默地板截斷照常配對。修後一律判無效 → `bench_pair_idx_invalid` 警語 + 位置配對退路;整值浮點(1.0)無歧義照整數接受(與 int 索引逐位一致) | `test_r21.py::test_bench_idx_type_anomalies_warn_and_fall_back`(三態參數化)、`::test_bench_idx_integral_floats_accepted_as_ints` |
| **索引重複** | 判無效(同一天算兩次=灌水配對樣本;R19 新防禦) | `test_r19.py::test_bench_idx_duplicates_invalid` |
| 剔列同步後 <2 對 | 誠實**略過**基準比較 + `bench_pair_too_few`(修前警語謊稱「退回位置配對」實際整卡跳過——措辭分流講真話) | `test_r19.py::test_bench_pair_too_few_honest_skip_wording` |
| 缺值剔列後座標失效 | keep_mask 同步剔除+重映射 | `test_r17.py::test_bench_pair_remaps_after_missing_drop` |
| 日期排序後座標失效 | sort_perm 反向重映射+時序重排 | `test_r19.py::test_unsorted_dates_benchmark_idx_remapped` |

## turnover(換手率)——R19 必修3

| 異常 | 政策 | 測試 |
|---|---|---|
| NaN/inf 或**負值** <5% | 該些期從成本計算剔除 + `turnover_nonfinite_dropped`(絕不填 0 低估成本;負換手=倒貼加分,同罪) | `test_r19.py::test_turnover_nonfinite_small_dropped_equals_hand`、`::test_turnover_negative_treated_as_invalid` |
| NaN/inf 或負值 ≥5% | **跳過** cost_stress + `turnover_nonfinite_skipped`(略過不算通過) | `test_r19.py::test_turnover_nonfinite_over_threshold_skips_cost_gate` |
| 非數字型別 | 警語 `turnover_invalid_type` + 跳閘 | `test_r19.py::test_bench_turnover_invalid_type_skipped_with_warning` |
| 長度與 returns 不符 | 截到共同長度 + 警語 `turnover_len_mismatch`(修前完全靜默截斷;R19b:長度檢查移到截斷/排序**之前**——修前 `tn[:n][sort_perm]` 先切齊長度,日期被排序過的資料上警語永不觸發) | `test_r19.py::test_turnover_length_mismatch_disclosed`、`::test_turnover_len_mismatch_fires_on_sorted_data` |
| 缺值剔列/日期排序後座標 | keep_mask / sort_perm 同步 | `test_r17.py::test_fwer_bench_cost_curve_remap_after_row_drop` |
| `cost_stress` 直呼(單元層最後防線) | 非有限對/負換手逐期剔除,不填 0 | `test_r19.py::test_cost_stress_unit_never_zero_fills` |

## n_trials(申報試驗數)

| 異常 | 政策 | 測試 |
|---|---|---|
| 非整數字串/不可解析 | 回退 1(不通縮)+ 警語 `n_trials_invalid` | `test_r19.py::test_n_trials_invalid_falls_back_to_one_with_warning` |
| ≤0 / 負值 | 回退 1 + 警語 `n_trials_invalid`(R19b:`=0` 修前被 `or 1` 當 falsy 靜默洗成 1 無警語——本格曾超宣稱,現為真) | 同上;`test_r19.py::test_n_trials_zero_falls_back_with_warning` |
| 小數(如 2.7) | `int()` 截斷(接受;文件化) | 政策文件化 |
| 極大值 | 合法且更保守(log 扣分 `many_trials_penalty`;matrix 另有欄數地板與雜訊硬化) | `test_engine.py`(既有) |
| 申報 < 引擎採用 | 措辭誠實分流(「通縮以 N 種計,你申報 M 種」) | `test_r17.py::test_trials_wording_discloses_declared_when_uplifted` |
| =1 | 判決句走專用模板(無多重檢定可扣,不再講「扣掉試了 1 種參數的多重檢定」;zh/en、rc_ 模板與 engine reasons 都分流) | `test_r19.py::test_dsr_wording_n_trials_one_no_deflation_language` |

## periods_per_year(年化頻率)

| 異常 | 政策 | 測試 |
|---|---|---|
| ≤0 / NaN / 不可解析 | 忽略 + 警語 `ppy_invalid` → 改日期推斷(無日期回退 252)。修前 `-1` 會讓年化吃 `sqrt(負數)` 靜默變 NaN;R19b:`=0`(數值)修前被 `if fallback:` 當 falsy 靜默跳過驗證無警語(字串 `"0"` 與 `-3` 反而有)——本格曾超宣稱,現為真 | `test_r19.py::test_ppy_invalid_warns_and_falls_back`、`::test_ppy_zero_ignored_with_warning` |
| 留空 + 日期壞 | 回退 252 + `ppy_fallback` 警語 | `test_engine.py::test_ppy_fallback_warning_on_bad_dates` |
| 極大正值 | **未防禦(接受)**——使用者明示優先(日內資料 ppy 本可達 50 萬級,無法立可靠上限);年化失真由使用者自負,`short_calendar_span` 部分兜底 | 誠實聲明 |
| 重複/亂序日期干擾推斷 | ppy 改在【日期完整性守衛之後】才推(R19:重複/亂序會扭曲平均步距) | `test_r19.py::test_unsorted_dates_sorted_matches_presorted`(排序等價隱含) |

## cost_bps_per_turnover(成本率)

| 異常 | 政策 | 測試 |
|---|---|---|
| 負值 | 跳閘 + `cost_bps_invalid`(負成本=倒貼,反保守) | `test_r19.py::test_cost_bps_negative_skips_with_warning` |
| NaN / 不可解析 | 跳閘 + `cost_bps_invalid` | 同一守衛(同分支) |
| 0 | 合法(零成本情境) | 政策文件化 |
| 極大值 | 合法且更保守(壓力測試本意) | 政策文件化 |

## 前端 CSV 解析層(app.js,R19 必修5a)

| 異常 | 政策 | 測試 |
|---|---|---|
| 引號包裹千分位 `"1,234.56"` | RFC4180 引號感知切割(`splitCSVLine`:引號內分隔符不切、`""` 跳脫);修前被撕成兩欄 → 100% 拒審且錯誤訊息誤導 | `verify_integrity` 情境6 |
| 引號內換行 | **不支援**(逐行解析前提;README 如實聲明) | 誠實聲明 |
| 編碼(UTF-8/Big5/cp950) | 多編碼嘗試、替代字元最少者勝 | 既有(`decodeFile`) |

---

*本表由 R19 整面掃建立,之後每加一個輸入通道或異常處理,先在此表補格、再寫測試。*
