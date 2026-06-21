"""质量告警检查器 — Phase J #48"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from packages.quality_monitor.aggregator import QualityMetric


@dataclass
class QualityAlert:
    alert_id: str
    tenant_id: str
    alert_type: str  # satisfaction_drop | bad_case_spike | rating_decline
    threshold: float
    current_value: float
    message: str
    created_at: float
    severity: str  # warning | critical


class AlertChecker:
    def check_satisfaction_drop(
        self,
        metric: QualityMetric,
        threshold: float = 0.7,
    ) -> QualityAlert | None:
        if metric.satisfaction_rate < threshold:
            severity = "critical" if metric.satisfaction_rate < threshold * 0.8 else "warning"
            return QualityAlert(
                alert_id=f"alert-{uuid.uuid4().hex[:8]}",
                tenant_id=metric.tenant_id,
                alert_type="satisfaction_drop",
                threshold=threshold,
                current_value=metric.satisfaction_rate,
                message=(
                    f"满意度 {metric.satisfaction_rate:.1%} 低于阈值 {threshold:.1%}"
                ),
                created_at=time.time(),
                severity=severity,
            )
        return None

    def check_bad_case_spike(
        self,
        metric: QualityMetric,
        threshold: int = 10,
    ) -> QualityAlert | None:
        if metric.bad_case_count > threshold:
            severity = "critical" if metric.bad_case_count > threshold * 2 else "warning"
            return QualityAlert(
                alert_id=f"alert-{uuid.uuid4().hex[:8]}",
                tenant_id=metric.tenant_id,
                alert_type="bad_case_spike",
                threshold=float(threshold),
                current_value=float(metric.bad_case_count),
                message=(
                    f"差评数 {metric.bad_case_count} 超过阈值 {threshold}"
                ),
                created_at=time.time(),
                severity=severity,
            )
        return None

    def check_rating_decline(
        self,
        current: QualityMetric,
        previous: QualityMetric,
        threshold: float = 0.5,
    ) -> QualityAlert | None:
        if previous.avg_rating > 0 and current.avg_rating > 0:
            decline = previous.avg_rating - current.avg_rating
            if decline > threshold:
                severity = "critical" if decline > threshold * 2 else "warning"
                return QualityAlert(
                    alert_id=f"alert-{uuid.uuid4().hex[:8]}",
                    tenant_id=current.tenant_id,
                    alert_type="rating_decline",
                    threshold=threshold,
                    current_value=current.avg_rating,
                    message=(
                        f"均分从 {previous.avg_rating:.2f} 降至 {current.avg_rating:.2f}，"
                        f"降幅 {decline:.2f} 超过阈值 {threshold}"
                    ),
                    created_at=time.time(),
                    severity=severity,
                )
        return None

    async def run_all_checks(
        self,
        tenant_id: str,
        satisfaction_threshold: float = 0.7,
        bad_case_threshold: int = 10,
        rating_decline_threshold: float = 0.5,
    ) -> list[QualityAlert]:
        from packages.quality_monitor.aggregator import get_quality_monitor

        agg = get_quality_monitor()
        if agg is None:
            return []

        alerts: list[QualityAlert] = []
        current = await agg.get_current(tenant_id)
        trend = await agg.get_trend(tenant_id, windows=2)

        a = self.check_satisfaction_drop(current, threshold=satisfaction_threshold)
        if a:
            alerts.append(a)

        b = self.check_bad_case_spike(current, threshold=bad_case_threshold)
        if b:
            alerts.append(b)

        if len(trend) >= 2:
            c = self.check_rating_decline(trend[-1], trend[-2], threshold=rating_decline_threshold)
            if c:
                alerts.append(c)

        return alerts
