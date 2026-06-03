# -*- coding: utf-8 -*-
# job_merger.py - Job合并器
"""
Job合并器 - 实现并行生产
职责：将两个job合并成一个MergedJob，完全模拟ProcessJob接口
"""
from typing import List, Optional
from models import ProcessJob, Sequence, Recipe, SequenceStep



class MergedJob:
    """
    合并Job - 完全模拟ProcessJob接口

    通过实现与ProcessJob相同的接口，使调度器无需修改任何代码即可处理并行生产。
    核心思路：
    1. wafer_collection 按 job1_w1, job2_w1, job1_w2, job2_w2 交替排列
    2. sequence_for_job 是两个job的sequence超集
    3. 每个wafer仍保持自己的 associated_sequence
    """

    def __init__(self, job1: ProcessJob, job2: ProcessJob):
        """
        初始化合并Job

        Args:
            job1: 第一个job
            job2: 第二个job
        """
        # 保存原始job引用（用于内部追踪，外部可选使用）
        self._job1 = job1
        self._job2 = job2

        # ===== 模拟 ProcessJob 的接口 =====

        # 1. process_job_id
        self.process_job_id = f"merged_{job1.process_job_id}_{job2.process_job_id}"

        # 2. 动态装载，初始为空
        self.wafer_collection = []
        # 追踪装载进度
        self._job1_index = 0
        self._job2_index = 0

        # 3. sequence_for_job - 合并超集
        self.sequence_for_job = self._merge_sequences(job1, job2)


    def _merge_sequences(self, job1: ProcessJob, job2: ProcessJob) -> Sequence:
        """
        合并两个job的sequence

        合并策略：
        1. sequence_steps: Union模式，包含两个job的所有不重复steps
        2. pre_lot_recipe: 合并两个recipe的target chambers
        3. pre_lot_recipe_condition_chamber_idle_time: 取两者最小值（更保守）
        4. post_lot_recipe: 合并两个recipe的target chambers
        5. interval_recipe: 禁用（交替调度打破了interval逻辑）

        Args:
            job1: 第一个job
            job2: 第二个job

        Returns:
            Sequence: 合并后的sequence
        """
        seq1 = job1.sequence_for_job
        seq2 = job2.sequence_for_job

        # 合并sequence_steps
        merged_steps = self._merge_sequence_steps(seq1.sequence_steps, seq2.sequence_steps)

        # 合并prelot recipe
        merged_prelot = self._merge_recipes(seq1.pre_lot_recipe, seq2.pre_lot_recipe)

        # 合并prelot condition time
        merged_prelot_condition = self._merge_condition_time(
            seq1.pre_lot_recipe_condition_chamber_idle_time,
            seq2.pre_lot_recipe_condition_chamber_idle_time,
            seq1.pre_lot_recipe,
            seq2.pre_lot_recipe
        )

        # 合并postlot recipe
        merged_postlot = self._merge_recipes(seq1.post_lot_recipe, seq2.post_lot_recipe)

        # 创建合并后的sequence
        merged_sequence = Sequence(
            sequence_name=f"merged_{seq1.sequence_name}_{seq2.sequence_name}",
            sequence_steps=merged_steps,
            pre_lot_recipe=merged_prelot,
            pre_lot_recipe_condition_chamber_idle_time=merged_prelot_condition,
            post_lot_recipe=merged_postlot,
            interval_recipe=None,  # 禁用interval
            interval_wafer_count=0
        )

        return merged_sequence

    def _merge_sequence_steps(self, steps1: List[SequenceStep],
                              steps2: List[SequenceStep]) -> List[SequenceStep]:
        """
        合并sequence_steps - Union模式

        策略：
        - 使用 target_modules 作为唯一标识符
        - 去重：如果两个step的target_modules相同，只保留一个
        - 保持原有的step对象（包含recipe等完整信息）

        Args:
            steps1: job1的steps
            steps2: job2的steps

        Returns:
            List[SequenceStep]: 合并后的steps
        """
        step_dict = {}

        # 添加job1的steps
        for step in steps1:
            # 使用target_modules作为key（排序后的tuple保证唯一性）
            key = tuple(sorted([m.value for m in step.target_modules]))
            if key not in step_dict:
                step_dict[key] = step

        # 添加job2的steps
        for step in steps2:
            key = tuple(sorted([m.value for m in step.target_modules]))
            if key not in step_dict:
                step_dict[key] = step

        # 返回合并后的steps列表
        return list(step_dict.values())

    def _merge_recipes(self, recipe1: Optional[Recipe],
                       recipe2: Optional[Recipe]) -> Optional[Recipe]:
        """
        合并两个recipe

        策略：
        - 如果都为None，返回None
        - 如果一个为None，返回另一个
        - 如果都存在，合并recipe_views（以module_id为key去重）

        Args:
            recipe1: 第一个recipe
            recipe2: 第二个recipe

        Returns:
            Optional[Recipe]: 合并后的recipe
        """
        # 两个都为None
        if not recipe1 and not recipe2:
            return None

        # 只有一个存在
        if not recipe1:
            return recipe2
        if not recipe2:
            return recipe1

        # 两个都存在：合并recipe_views
        merged_views_dict = {}

        # 添加recipe1的views
        for view in recipe1.recipe_views:
            key = view.module_id.value
            merged_views_dict[key] = view

        # 添加recipe2的views（如果module_id不重复）
        for view in recipe2.recipe_views:
            key = view.module_id.value
            if key not in merged_views_dict:
                merged_views_dict[key] = view

        # 创建合并后的recipe
        merged_recipe = Recipe(
            recipe_name=f"merged_{recipe1.recipe_name}_{recipe2.recipe_name}",
            recipe_views=list(merged_views_dict.values())
        )

        return merged_recipe

    def _merge_condition_time(self, time1: float, time2: float,
                              recipe1: Optional[Recipe],
                              recipe2: Optional[Recipe]) -> float:
        """
        合并prelot condition time

        策略：
        - 如果两个job都有prelot recipe，取两者condition time的最小值（更保守）
        - 如果只有一个有prelot recipe，使用那个的condition time
        - 如果都没有，返回0

        Args:
            time1: job1的condition time
            time2: job2的condition time
            recipe1: job1的prelot recipe
            recipe2: job2的prelot recipe

        Returns:
            float: 合并后的condition time
        """
        if recipe1 and recipe2:
            # 两个都有prelot，取最小值
            return min(time1, time2)
        elif recipe1:
            return time1
        elif recipe2:
            return time2
        else:
            return 0.0

    def has_next_wafer(self) -> bool:
        """检查是否还有wafer可以装载"""
        return (self._job1_index < len(self._job1.wafer_collection) or
                self._job2_index < len(self._job2.wafer_collection))

    def get_next_wafer_candidates(self):
        """
        获取下一批候选wafer

        Returns:
            list: [(wafer, job_name), ...] 最多2个候选
        """
        candidates = []

        if self._job1_index < len(self._job1.wafer_collection):
            candidates.append((
                self._job1.wafer_collection[self._job1_index],
                'job1'
            ))

        if self._job2_index < len(self._job2.wafer_collection):
            candidates.append((
                self._job2.wafer_collection[self._job2_index],
                'job2'
            ))

        return candidates

    def mark_wafer_loaded(self, job_name: str):
        """
        标记某个job的wafer已装载

        Args:
            job_name: 'job1' 或 'job2'
        """
        if job_name == 'job1':
            wafer = self._job1.wafer_collection[self._job1_index]
            self.wafer_collection.append(wafer)
            self._job1_index += 1
        elif job_name == 'job2':
            wafer = self._job2.wafer_collection[self._job2_index]
            self.wafer_collection.append(wafer)
            self._job2_index += 1

def merge_jobs(job1: ProcessJob, job2: ProcessJob) -> MergedJob:
    """
    合并两个job为一个MergedJob（并行生产）

    使用方式：
```python
    # 创建两个job
    job1 = create_job1()
    job2 = create_job2()

    # 合并为并行job
    merged_job = merge_jobs(job1, job2)

    # 加入调度队列（调度器无需修改）
    main_scheduler.job_queue.append(merged_job)
    main_scheduler.start_system()
```

    Args:
        job1: 第一个job
        job2: 第二个job

    Returns:
        MergedJob: 合并后的job，接口与ProcessJob完全一致
    """
    return MergedJob(job1, job2)


