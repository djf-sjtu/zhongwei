# 中威半导体调度仿真器

PVD / CVD 平台调度系统仿真，纯 Python，无第三方依赖，直接用系统解释器运行。

---

## 目录结构

```
CVD20260127/        CVD 平台（批处理 wafer，双臂 TMB 机械臂）
PVD20260124代码重构/ PVD 平台（单片 wafer，单臂 TM 机械臂）
```

两个平台架构相同，配置和部分执行逻辑有差异。

---

## 运行仿真

```bash
# CVD
cd CVD20260127
python3 main_scheduler.py --scenario <N>

# PVD
cd PVD20260124代码重构
python3 main_scheduler.py --scenario <N>
```

### CVD 场景

| `--scenario` | 内容 | 故障时间 |
|---|---|---|
| 1 | 单 Job，basic 工艺，5 片 wafer | 无 |
| 2 | 单 Job + BULK_A 故障/恢复 | t=60s / t=180s |
| 3 | 两 Job 合并，联合故障验收（重规划 + 冻结 + TEMP_PARK）| t=200s / t=600s |
| 4 | 端到端全流程验收（含 unfault 重平衡）| t=230s / t=560s |

日志实时打印到终端，同时写入 `logs/` 目录（按时间命名）。

---

## 关键配置（`config/system_config.py`）

| 参数 | 说明 | 默认值 |
|---|---|---|
| `TIME_SCALE` | 仿真加速比。`0.1` = 10倍加速；`1.0` = 真实时速 | `0.1` |
| `PROCESS_TIMES['NUC']` | NUC 工艺时长（秒，真实值） | `900` |
| `PROCESS_TIMES['BULK']` | BULK 工艺时长（秒，真实值） | `3600` |
| `CHAMBER_MODULES` | 在线 chamber 列表，注释掉某行即禁用该 chamber | — |

> 实际执行时长 = 配置值 × TIME_SCALE。  
> 故障注入时间（`main_scheduler.py` 里的 `threading.Timer`）单位是真实秒，需与 TIME_SCALE 配合校准。

---

## 架构分层

```
main_scheduler.py          入口：构建 Job/Wafer，运行场景
        │
job_coordinator.py         顶层编排：Job 队列、EFEM 装载、周期检查
        │
services/
  planning/path_planner.py 路径规划：为 wafer 计算完整路径和时间
  scheduling/              调度核心：任务分配、资源选择、机械臂选择
execution/                 执行层：传输任务、chamber 任务、宏任务
        │
domain/                    跨层业务规则：资源验证、wafer 服务
entities/                  运行时状态：SchedulingWafer/Chamber/Robot/LL/TBS
models/                    纯数据模型：Wafer、ProcessJob、Sequence、Recipe
config/                    配置数据（SystemConfig）和查询接口（ConfigService）
```

依赖方向严格单向向下，低层不引用高层。

---

## 重要算法

### 1. 路径规划（PathPlanner）

`plan_wafer_route_from_foup(wafer)` 为 wafer 规划从 FOUP 出发的完整路径：

1. 遍历工艺序列的剩余步骤，对每步调用 `_find_best_chamber`（取当前队列最短的可用 chamber）
2. 计算每步的 `estimated_arrival` / `estimated_departure`，若 wafer 到达比 chamber 就绪早则推迟 FOUP 出发时间（`leave_foup_time`）
3. 识别"瓶颈 chamber"（queue 末端是 wafer_process 的 chamber），提前 `SWITCH_ADVANCE_TIME` 出发以实现 switch 操作
4. 调用 `_reserve_chamber_slots` 将预订写入 chamber.task_queue（`reserve_` 前缀任务）

延迟传播：wafer 实际到达晚于预期时，`update_path_if_delayed` 级联更新后续步骤和同 chamber 队列中的其他 wafer。

### 2. 调度与资源分配

`SchedulerCore.assign_next_task(wafer)` 从 `assignment_queue` 取下一条传输任务：

- **目标位置动态选择**（LL、TBS）：运行时选最空闲的空闲槽
- **静态预绑定**（chamber）：路径规划阶段已确定，assignment_queue 中直接写入目标 ID
- **`is_next_step_ready`**：chamber 需满足 `busy==0 AND task_queue[0].wafer_id == wafer.wafer_id`，确保严格按队列顺序进入

### 3. Chamber 故障处理

**故障注入** `fault_chamber(chamber_id)`：
- 设置 `chamber.is_faulted = True`
- 对所有 path_plan 包含该 chamber 的 wafer 调用 `replan_for_faulted_chamber`，改道到最优替代 chamber
- 若 wafer 正在该 chamber 加工中，任务"冻结"（`frozen_task`），不出片，等待 unfault

**`replan_for_faulted_chamber(wafer, faulted_id)`**：
1. 找 path_plan 中目标为 `faulted_id` 的步骤
2. 从剩余可用同类 chamber 中选就绪时间最早的作为替代
3. 重算该步骤及后续步骤时间
4. 从故障 chamber 的 task_queue 移除预订，按 estimated_arrival 有序插入新 chamber
5. **同时更新** `assignment_queue[i].to_location` 和 `assignment_queue[i+1].from_location`（两者必须同步，否则机械臂释放源位置时操作错误 chamber 导致死锁）

### 4. TEMP_PARK（临时停靠）

当所有同类 chamber 均故障，wafer 找不到替代目标时：
- `temp_park_wafer(wafer, insert_step_idx)` 在 path_plan 中插入一个 TEMP_PARK 步骤
- 选择一个空闲 TBS 作为临时停靠点，预订防止其他 wafer 抢占
- wafer 搬到 TBS 等待，周期检查发现 chamber 恢复后重新调度

### 5. Unfault 重平衡（_rebalance_after_unfault）

`unfault_chamber(chamber_id)` 恢复时：
1. 调用 `release_frozen_task` 立即完成冻结中的加工任务，wafer 继续流转
2. 调用 `_rebalance_after_unfault(chamber_id)` 重新均衡负载：
   - 搜索**全量** `system.wafers`（包含仍在 FOUP 的 wafer），找 path_plan 目标为兄弟 chamber 的候选
   - 用**实时 ETA**（`_estimate_arrival`）排序，优先迁移即将到达的 wafer
   - 逐一比较 `_get_chamber_ready_time(recovered)` vs `_get_chamber_ready_time(sibling)`，recovered 更快才迁移，自然均衡后停止（自适应，不硬编码比例）

---

## MergedJob（并行 Job）

`common/job_merger.py` 将两个 Job 合并为一个逻辑 Job，交错装载 wafer：
- 每次装载时选两个 Job 中 `leave_time`（`calculate_leave_time_without_reservation`）最早的 wafer
- `JobCoordinator` 在 `_on_wafer_completed` 和 `_load_next_wafer` 中对 `MergedJob` 特殊处理

---

## CVD 特有：批处理（BatchWafer）

CVD 平台机械臂一次抓取两片 wafer（left + right），封装为 `BatchWafer`：
- `entities/batch_wafer.py` 负责将单片 wafer 两两配对
- 传输日志按单片分行输出（各自的 wafer_id 和 source_foup 信息）
- 所有调度逻辑以 batch 为单位，path_plan / assignment_queue 挂在 batch 上
