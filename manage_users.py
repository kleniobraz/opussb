#!/usr/bin/env python3
"""Gestão de usuários do OpusSB (login interno).

Uso:
  ./venv/bin/python manage_users.py add EMAIL [EMAIL ...] --password SENHA_PADRAO
  ./venv/bin/python manage_users.py add EMAIL [EMAIL ...]        # pergunta a senha
  ./venv/bin/python manage_users.py reset EMAIL --password SENHA # reseta p/ senha padrão
  ./venv/bin/python manage_users.py list
  ./venv/bin/python manage_users.py delete EMAIL

Todo usuário criado/resetado entra com must_change_password=1: no primeiro acesso
o app obriga a troca da senha antes de liberar upload.
"""
import sys
import getpass
import argparse

from werkzeug.security import generate_password_hash

import db


def _get_password(arg_password: str | None) -> str:
    pw = arg_password or getpass.getpass("Senha padrão para os usuários: ")
    if len(pw) < 8:
        sys.exit("Erro: a senha precisa ter ao menos 8 caracteres.")
    return pw


def cmd_add(emails, password, active=False):
    pw = _get_password(password)
    h = generate_password_hash(pw)
    must_change = not active
    for email in emails:
        db.upsert_user(email, h, must_change=must_change)
        suffix = "(já ativo, sem troca obrigatória)" if active else "(troca obrigatória no 1º acesso)"
        print(f"[ok] cadastrado: {email.strip().lower()} {suffix}")


def cmd_reset(emails, password, active=False):
    pw = _get_password(password)
    h = generate_password_hash(pw)
    must_change = not active
    for email in emails:
        if not db.get_user(email):
            print(f"[skip] não existe: {email}")
            continue
        db.upsert_user(email, h, must_change=must_change)
        print(f"[ok] resetado: {email.strip().lower()}")


def cmd_list():
    users = db.list_users()
    if not users:
        print("(nenhum usuário cadastrado)")
        return
    for u in users:
        flag = "troca pendente" if u["must_change_password"] else "ativo"
        print(f"  {u['email']:40s}  {flag:15s}  desde {u['created_at']}")


def cmd_delete(emails):
    with db._conn() as conn:
        for email in emails:
            conn.execute("DELETE FROM users WHERE email = ?", (email.strip().lower(),))
            print(f"[ok] removido: {email.strip().lower()}")


def main():
    db.init_users()
    p = argparse.ArgumentParser(description="Gestão de usuários do OpusSB")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="cadastra usuários com senha padrão")
    a.add_argument("emails", nargs="+")
    a.add_argument("--password", default=None)
    a.add_argument("--active", action="store_true",
                   help="cria já ativo (sem troca obrigatória); útil pra testes/scripts")

    r = sub.add_parser("reset", help="reseta usuários para a senha padrão")
    r.add_argument("emails", nargs="+")
    r.add_argument("--password", default=None)
    r.add_argument("--active", action="store_true",
                   help="reseta já ativo (sem troca obrigatória)")

    sub.add_parser("list", help="lista usuários")

    d = sub.add_parser("delete", help="remove usuários")
    d.add_argument("emails", nargs="+")

    args = p.parse_args()
    if args.cmd == "add":
        cmd_add(args.emails, args.password, args.active)
    elif args.cmd == "reset":
        cmd_reset(args.emails, args.password, args.active)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "delete":
        cmd_delete(args.emails)


if __name__ == "__main__":
    main()
