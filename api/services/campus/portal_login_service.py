"""Store and switch administrator-uploaded portal login pages.

The uploaded file lives beside the portal's own assets: the Campus API container
writes the directory (``CAMPUS_PORTAL_LOGIN_STORAGE_DIR``) and the read-only
portal container mounts the same directory as ``/custom``. Activating a page
copies its stored file to ``index.html`` in that directory, which the portal
nginx serves ahead of its built-in page; deactivating removes it again. Nothing
is served that has not passed :mod:`services.campus.portal_login_page` first.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from configs import dify_config
from models.campus import CampusAuditEvent, CampusPortalLoginPage
from services.campus.errors import CampusValidationError
from services.campus.portal_login_page import (
    MAX_PORTAL_LOGIN_BYTES,
    PortalLoginValidation,
    portal_login_digest,
    validate_portal_login_page,
)

ACTIVE_FILENAME = "index.html"
UPLOADS_DIRNAME = "uploads"


@dataclass(frozen=True)
class PortalLoginPageState:
    """What the administration portal shows: the live page plus the library."""

    active: CampusPortalLoginPage | None
    pages: tuple[CampusPortalLoginPage, ...]


class PortalLoginPageService:
    def __init__(self, *, session: Session, storage_dir: str | None = None) -> None:
        self._session = session
        self._dir = Path(storage_dir or dify_config.CAMPUS_PORTAL_LOGIN_STORAGE_DIR)
        self._uploads = self._dir / UPLOADS_DIRNAME
        self._active_path = self._dir / ACTIVE_FILENAME

    # -- reads -----------------------------------------------------------
    def state(self) -> PortalLoginPageState:
        pages = tuple(
            self._session.scalars(
                select(CampusPortalLoginPage).order_by(CampusPortalLoginPage.created_at.desc())
            )
        )
        active = next((page for page in pages if page.is_active), None)
        return PortalLoginPageState(active=active, pages=pages)

    def validation_report(self, page: CampusPortalLoginPage) -> dict[str, object]:
        return json.loads(page.validation_json)

    # -- writes ----------------------------------------------------------
    def upload(
        self, *, filename: str, content: bytes, actor_account_id: str
    ) -> tuple[CampusPortalLoginPage, PortalLoginValidation]:
        """Validate an uploaded page and store it. A failing page is refused."""
        if len(content) > MAX_PORTAL_LOGIN_BYTES:
            raise CampusValidationError(
                f"登录页文件不能超过 {MAX_PORTAL_LOGIN_BYTES // 1024} KB。"
            )
        try:
            html = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CampusValidationError("登录页必须是 UTF-8 编码的 HTML 文件。") from error

        report = validate_portal_login_page(html)
        if not report.ok:
            raise CampusValidationError("登录页未通过审核：" + " ".join(report.errors))

        page = CampusPortalLoginPage(
            filename=filename or "login.html",
            size_bytes=len(content),
            digest=portal_login_digest(html),
            storage_key="",
            validation_json=json.dumps(
                {"errors": list(report.errors), "warnings": list(report.warnings), "checks": list(report.checks)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            is_active=False,
            uploaded_by_account_id=actor_account_id,
        )
        self._session.add(page)
        self._session.flush()
        page.storage_key = f"{UPLOADS_DIRNAME}/{page.id}.html"
        self._write(self._dir / page.storage_key, content)
        self._audit("portal_login_page.uploaded", page, actor_account_id, {"filename": page.filename})
        self._session.commit()
        return page, report

    def activate(self, page_id: str, *, actor_account_id: str) -> CampusPortalLoginPage:
        """Copy the chosen page in front of the built-in one."""
        page = self._get(page_id)
        source = self._dir / page.storage_key
        if not source.exists():
            raise CampusValidationError("登录页文件已丢失，请重新上传后再启用。")
        self._write(self._active_path, source.read_bytes())
        for other in self._active_pages():
            other.is_active = False
        page.is_active = True
        self._audit("portal_login_page.activated", page, actor_account_id, {"filename": page.filename})
        self._session.commit()
        return page

    def deactivate(self, *, actor_account_id: str) -> None:
        """Put the built-in login page back."""
        active = self._active_page()
        self._active_path.unlink(missing_ok=True)
        for page in self._active_pages():
            page.is_active = False
        if active is not None:
            self._audit("portal_login_page.deactivated", active, actor_account_id, {})
        self._session.commit()

    def delete(self, page_id: str, *, actor_account_id: str) -> None:
        page = self._get(page_id)
        if page.is_active:
            raise CampusValidationError("请先停用该登录页，再删除。")
        (self._dir / page.storage_key).unlink(missing_ok=True)
        self._audit("portal_login_page.deleted", page, actor_account_id, {"filename": page.filename})
        self._session.delete(page)
        self._session.commit()

    # -- helpers ---------------------------------------------------------
    def _get(self, page_id: str) -> CampusPortalLoginPage:
        page = self._session.get(CampusPortalLoginPage, page_id)
        if page is None:
            raise CampusValidationError("登录页不存在。")
        return page

    def _active_page(self) -> CampusPortalLoginPage | None:
        return self._session.scalar(
            select(CampusPortalLoginPage).where(CampusPortalLoginPage.is_active.is_(True))
        )

    def _active_pages(self) -> list[CampusPortalLoginPage]:
        """Every row the one-active-page invariant has to clear."""
        return list(
            self._session.scalars(
                select(CampusPortalLoginPage).where(CampusPortalLoginPage.is_active.is_(True))
            )
        )

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        shutil.move(str(temporary), str(path))

    def _audit(
        self, action: str, page: CampusPortalLoginPage, actor_account_id: str, details: dict[str, object]
    ) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="portal_login_page",
                target_id=page.id,
                details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":")),
            )
        )
