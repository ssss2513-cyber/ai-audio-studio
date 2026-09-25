# -*- coding: utf-8 -*-
import os
import sqlite3
import hashlib
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "users.db"))

class DatabaseManager:
    @staticmethod
    def get_connection():
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init_db(cls):
        """데이터베이스 및 테이블 스키마 초기화"""
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            # 1. 회원 정보 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    gemini_api_key TEXT DEFAULT '',
                    gpt_sovits_url TEXT DEFAULT 'http://127.0.0.1:9880/tts',
                    preferred_engine TEXT DEFAULT 'supertonic',
                    custom_settings TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
            # 2. 로그인 세션 유지 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
                )
            """)
            conn.commit()

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
        """PBKDF2-HMAC-SHA256 기반 비밀번호 해싱 (100,000회 반복)"""
        if salt is None:
            salt = secrets.token_bytes(16)
        pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return salt.hex(), pwd_hash.hex()

    @classmethod
    def register_user(cls, username: str, password: str) -> Tuple[bool, str]:
        """신규 회원 등록"""
        username = username.strip()
        if not username:
            return False, "아이디를 입력해주세요."
        if len(username) < 2 or len(username) > 20:
            return False, "아이디는 2자 이상 20자 이하여야 합니다."
        if not password or len(password) < 4:
            return False, "비밀번호는 최소 4자 이상이어야 합니다."

        salt_hex, hash_hex = cls._hash_password(password)

        try:
            with cls.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (username, password_hash, salt, last_login)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (username, hash_hex, salt_hex))
                conn.commit()
            return True, f"'{username}'님, 회원가입이 완료되었습니다! 로그인해주세요."
        except sqlite3.IntegrityError:
            return False, "이미 사용 중인 아이디입니다. 다른 아이디를 입력해주세요."
        except Exception as e:
            return False, f"회원가입 실패: {str(e)}"

    @classmethod
    def authenticate_user(cls, username: str, password: str) -> Tuple[bool, Any]:
        """로그인 인증 검증"""
        username = username.strip()
        if not username or not password:
            return False, "아이디와 비밀번호를 모두 입력해주세요."

        try:
            with cls.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
                row = cursor.fetchone()
                if not row:
                    return False, "존재하지 않는 아이디입니다."

                salt = bytes.fromhex(row["salt"])
                expected_hash = row["password_hash"]
                _, input_hash = cls._hash_password(password, salt)

                if not secrets.compare_digest(expected_hash, input_hash):
                    return False, "비밀번호가 일치하지 않습니다."

                # 마지막 로그인 시간 갱신
                cursor.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE username = ?", (username,))
                conn.commit()

                profile = dict(row)
                profile.pop("password_hash", None)
                profile.pop("salt", None)
                if profile.get("custom_settings"):
                    try:
                        profile["custom_settings"] = json.loads(profile["custom_settings"])
                    except Exception:
                        profile["custom_settings"] = {}
                else:
                    profile["custom_settings"] = {}
                return True, profile
        except Exception as e:
            return False, f"로그인 오류: {str(e)}"

    @classmethod
    def create_session(cls, username: str, days: int = 30) -> str:
        """지속 세션 토큰 생성 (새로고침 유지용)"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=days)
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (token, username, expires_at)
                VALUES (?, ?, ?)
            """, (token, username, expires_at.isoformat()))
            conn.commit()
        return token

    @classmethod
    def get_user_from_session(cls, token: str) -> Optional[Dict[str, Any]]:
        """세션 토큰으로 사용자 프로필 복원"""
        if not token:
            return None
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.* FROM users u
                JOIN sessions s ON u.username = s.username
                WHERE s.token = ? AND datetime(s.expires_at) > datetime('now')
            """, (token,))
            row = cursor.fetchone()
            if row:
                profile = dict(row)
                profile.pop("password_hash", None)
                profile.pop("salt", None)
                if profile.get("custom_settings"):
                    try:
                        profile["custom_settings"] = json.loads(profile["custom_settings"])
                    except Exception:
                        profile["custom_settings"] = {}
                else:
                    profile["custom_settings"] = {}
                return profile
        return None

    @classmethod
    def delete_session(cls, token: str):
        """세션 토큰 삭제 (로그아웃)"""
        if not token:
            return
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()

    @classmethod
    def update_user_api_keys(
        cls,
        username: str,
        gemini_api_key: Optional[str] = None,
        gpt_sovits_url: Optional[str] = None,
        preferred_engine: Optional[str] = None,
        custom_settings: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """사용자의 API 키 및 환경설정 영구 저장"""
        username = username.strip()
        if not username:
            return False, "로그인이 필요한 작업입니다."

        updates = []
        params = []
        if gemini_api_key is not None:
            updates.append("gemini_api_key = ?")
            params.append(gemini_api_key.strip())
        if gpt_sovits_url is not None:
            updates.append("gpt_sovits_url = ?")
            params.append(gpt_sovits_url.strip())
        if preferred_engine is not None:
            updates.append("preferred_engine = ?")
            params.append(preferred_engine.strip())
        if custom_settings is not None:
            updates.append("custom_settings = ?")
            params.append(json.dumps(custom_settings, ensure_ascii=False))

        if not updates:
            return True, "변경된 설정이 없습니다."

        params.append(username)
        set_clause = ", ".join(updates)
        query = f"UPDATE users SET {set_clause} WHERE username = ?"

        try:
            with cls.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                conn.commit()
            return True, f"'{username}'님의 계정에 설정 및 API 키가 영구 저장되었습니다!"
        except Exception as e:
            return False, f"설정 저장 실패: {str(e)}"

    @classmethod
    def get_user_profile(cls, username: str) -> Optional[Dict[str, Any]]:
        """사용자 최신 프로필 정보 조회"""
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            if row:
                profile = dict(row)
                profile.pop("password_hash", None)
                profile.pop("salt", None)
                if profile.get("custom_settings"):
                    try:
                        profile["custom_settings"] = json.loads(profile["custom_settings"])
                    except Exception:
                        profile["custom_settings"] = {}
                else:
                    profile["custom_settings"] = {}
                return profile
        return None

# 데이터베이스 자동 초기화 실행
DatabaseManager.init_db()
