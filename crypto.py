import base64
import hashlib
import urllib.parse
import re
import asyncio
import discord
from discord.ext import commands

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
import random

# ── Helpers ─────────────────────────────────────────────────────────────

def is_prime(n: int) -> bool:
    """Miller-Rabin primality test."""
    if n < 2:
        return False
    if n == 2 or n == 3:
        return True
    if n % 2 == 0:
        return False
    r, d = 0, n - 1
    while d % 2 == 0:
        r += 1
        d //= 2
    witnesses = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
    for a in witnesses:
        if a >= n:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True

def generate_prime(bits: int = 512) -> int:
    """Generate a random prime of given bit length."""
    while True:
        n = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if is_prime(n):
            return n


def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def mod_inverse(e: int, phi: int) -> int | None:
    """Extended Euclidean Algorithm to find modular inverse."""
    def extended_gcd(a, b):
        if a == 0:
            return b, 0, 1
        g, x, y = extended_gcd(b % a, a)
        return g, y - (b // a) * x, x

    g, x, _ = extended_gcd(e % phi, phi)
    if g != 1:
        return None
    return x % phi


def rsa_generate_keypair(bits: int = 512) -> dict:
    """Generate RSA keypair. Returns public key, private key, and prime details."""
    p = generate_prime(bits // 2)
    q = generate_prime(bits // 2)
    while q == p:
        q = generate_prime(bits // 2)

    n   = p * q
    phi = (p - 1) * (q - 1)

    e = 65537
    if gcd(e, phi) != 1:
        e = 3
        while gcd(e, phi) != 1:
            e += 2

    d = mod_inverse(e, phi)

    return {
        "public":  (e, n),
        "private": (d, n),
        "p":   p,
        "q":   q,
        "phi": phi
    }


def rsa_encrypt(message: str, e: int, n: int) -> list[int]:
    """Encrypt a string message character by character using public key (e, n)."""
    return [pow(ord(c), e, n) for c in message]


def rsa_decrypt(ciphertext: list[int], d: int, n: int) -> str:
    """Decrypt RSA ciphertext back to string using private key (d, n)."""
    return "".join(chr(pow(c, d, n)) for c in ciphertext)


def format_key(label: str, e_or_d: int, n: int) -> str:
    return f"**{label}**\ne/d = `{e_or_d}`\nn   = `{n}`"


def is_readable(text: str) -> bool:
    if not text:
        return False
    printable = sum(c.isprintable() for c in text)
    return (printable / len(text)) > 0.85


def is_base64(s: str) -> bool:
    try:
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False


def is_hex(s: str) -> bool:
    return bool(re.fullmatch(r'[0-9a-fA-F]+', s)) and len(s) % 2 == 0


def is_url_encoded(s: str) -> bool:
    return "%" in s or "+" in s


def decode_base64(s: str) -> str | None:
    try:
        return base64.b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        return None


def decode_hex(s: str) -> str | None:
    try:
        return bytes.fromhex(s).decode("utf-8", errors="ignore")
    except Exception:
        return None


def decode_url(s: str) -> str | None:
    try:
        return urllib.parse.unquote(s)
    except Exception:
        return None


def xor_cipher(text: str, key: int) -> str:
    return "".join(chr(ord(c) ^ key) for c in text)


def rot_cipher(text: str, shift: int) -> str:
    result = []
    for c in text:
        if c.isalpha():
            base = ord('a') if c.islower() else ord('A')
            result.append(chr((ord(c) - base + shift) % 26 + base))
        else:
            result.append(c)
    return "".join(result)


def encode_all(s: str) -> dict:
    return {
        "Base64": base64.b64encode(s.encode()).decode(),
        "Hex": s.encode().hex(),
        "URL": urllib.parse.quote(s),
    }


def generate_hashes(s: str) -> dict:
    enc = s.encode()
    return {
        "MD5": hashlib.md5(enc).hexdigest(),
        "SHA1": hashlib.sha1(enc).hexdigest(),
        "SHA256": hashlib.sha256(enc).hexdigest(),
        "SHA512": hashlib.sha512(enc).hexdigest(),
    }


def decode_once(s: str) -> list[tuple[str, str]]:
    results = []

    if is_base64(s):
        decoded = decode_base64(s)
        if decoded and decoded != s:
            results.append(("Base64", decoded))

    if is_hex(s):
        decoded = decode_hex(s)
        if decoded and decoded != s:
            results.append(("Hex", decoded))

    if is_url_encoded(s):
        decoded = decode_url(s)
        if decoded and decoded != s:
            results.append(("URL", decoded))

    return results


def multilayer_decode(text: str, max_depth: int = 5) -> list[str]:
    current = text
    steps = []

    for _ in range(max_depth):
        results = decode_once(current)

        if not results:
            break

        method, decoded = results[0]

        if not is_readable(decoded):
            break

        steps.append(f"{method} → `{decoded}`")

        if decoded == current:
            break

        current = decoded

    return steps


# ── Cog ─────────────────────────────────────────────────────────────────

class Crypto(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


   
    # ── HASH COMMAND ─────────────────────────────────────────────────────

    @commands.command(name="hash")
    async def hash_cmd(self, ctx: commands.Context, *, text: str):

        hashes = generate_hashes(text)

        lines = ["**Generated Hashes:**"]
        for k, v in hashes.items():
            lines.append(f"{k}: `{v}`")

        await ctx.send("\n".join(lines))


    # ── ENCODE COMMAND ───────────────────────────────────────────────────

    @commands.command(name="encode")
    async def encode_cmd(self, ctx: commands.Context, method_or_text: str, *, text: str = None):

        if text is None:

            encodings = encode_all(method_or_text)

            lines = ["**Automatic Encodings:**"]
            for k, v in encodings.items():
                lines.append(f"{k}: `{v}`")

            await ctx.send("\n".join(lines))
            return

        method = method_or_text.lower()

        if method == "base64":
            result = base64.b64encode(text.encode()).decode()

        elif method == "hex":
            result = text.encode().hex()

        elif method == "url":
            result = urllib.parse.quote(text)

        elif method.startswith("rot"):

            try:
                shift = int(method.replace("rot", ""))
                result = rot_cipher(text, shift)

            except ValueError:
                await ctx.send("Usage: `$encode rot<N> <text>`")
                return

        elif method == "xor":

            parts = text.split(" ", 1)

            if len(parts) < 2:
                await ctx.send("Usage: `$encode xor <key> <text>`")
                return

            try:
                key = int(parts[0])
            except ValueError:
                await ctx.send("XOR key must be an integer.")
                return

            result = xor_cipher(parts[1], key)

        else:
            await ctx.send("Supported methods: base64, hex, url, rot<N>, xor")
            return

        await ctx.send(f"**{method.upper()}** → `{result}`")


    # ── DECODE COMMAND ───────────────────────────────────────────────────

    @commands.command(name="decode")
    async def decode_cmd(self, ctx: commands.Context, method_or_text: str, *, text: str = None):

        if text is None:

            steps = multilayer_decode(method_or_text)

            if not steps:
                await ctx.send("No decoding method succeeded.")
                return

            lines = ["**Auto Multi-layer Decoding:**"] + steps

            await ctx.send("\n".join(lines))
            return

        method = method_or_text.lower()

        if method == "base64":
            decoded = decode_base64(text)

        elif method == "hex":
            decoded = decode_hex(text)

        elif method == "url":
            decoded = decode_url(text)

        elif method.startswith("rot"):

            try:
                shift = int(method.replace("rot", ""))
                decoded = rot_cipher(text, -shift)

            except ValueError:
                await ctx.send("Usage: `$decode rot<N> <text>`")
                return

        elif method == "xor":

            parts = text.split(" ", 1)

            if len(parts) < 2:
                await ctx.send("Usage: `$decode xor <key> <text>`")
                return

            try:
                key = int(parts[0])
            except ValueError:
                await ctx.send("XOR key must be an integer.")
                return

            decoded = xor_cipher(parts[1], key)

        else:
            await ctx.send("Supported methods: base64, hex, url, rot<N>, xor")
            return

        if not decoded:
            await ctx.send("Invalid input or decoding failed.")
            return

        await ctx.send(f"**{method.upper()}** → `{decoded}`")


    # RSA COMMANDS ═══════════
    @commands.command(name="rsagen")
    async def rsa_gen(self, ctx: commands.Context, bits: int = 512):
        """
        Generate an RSA keypair.
        Usage: $rsagen [bits]   (256, 512, or 1024 — default 512)
        """
        if bits not in (256, 512, 1024):
            await ctx.send("Supported key sizes: `256`, `512`, `1024`.")
            return

        async with ctx.channel.typing():
            keypair = await asyncio.to_thread(rsa_generate_keypair, bits)

        e, n = keypair["public"]
        d, _ = keypair["private"]

        lines = [
            f"**RSA Keypair Generated ({bits}-bit)**",
            format_key("Public Key", e, n),
            format_key("Private Key", d, n),
            "",
            "For learning/demo purposes only — not secure for real-world use.",
        ]
        await ctx.send("\n".join(lines))

    @commands.command(name="rsaencrypt")
    async def rsa_encrypt_cmd(self, ctx: commands.Context, e: int, n: int, *, message: str):
        """
        Encrypt a message with an RSA public key.
        Usage: $rsaencrypt <e> <n> <message>
        Output is space-separated integers — one per character.
        Arguments:
            e          Public exponent of the RSA key.
            n          Modulus of the RSA key.
            message    The message string to encrypt.
        """
        if len(message) > 100:
            await ctx.send("Message too long. Keep it under 100 characters for RSA.")
            return

        if any(ord(c) >= n for c in message):
            await ctx.send("Your `n` value is too small for this message. Use a larger keypair.")
            return

        async with ctx.channel.typing():
            ciphertext = await asyncio.to_thread(rsa_encrypt, message, e, n)

        cipher_str = " ".join(str(x) for x in ciphertext)
        result     = f"**RSA Encrypted:**\n`{cipher_str}`"

        if len(result) > 1990:
            import io
            await ctx.send("**RSA Encrypted (output too large, sent as file):**")
            file = discord.File(io.BytesIO(cipher_str.encode()), filename="rsa_ciphertext.txt")
            await ctx.send(file=file)
        else:
            await ctx.send(result)

    @commands.command(name="rsadecrypt")
    async def rsa_decrypt_cmd(self, ctx: commands.Context, d: int, n: int, *, ciphertext: str):
        """
        Decrypt RSA ciphertext with a private key.
        Usage: $rsadecrypt <d> <n> <space-separated integers>
        Arguments:
            d          Private exponent of the RSA key.
            n          Modulus of the RSA key (same value used during encryption).
            ciphertext Space-separated integers representing the encrypted message blocks.
        """
        try:
            cipher_ints = [int(x) for x in ciphertext.strip().split()]
        except ValueError:
            await ctx.send("Ciphertext must be space-separated integers. Example:\n`$rsadecrypt <d> <n> 2081 2367 3216 3216 2324`")
            return

        try:
            async with ctx.channel.typing():
                plaintext = await asyncio.to_thread(rsa_decrypt, cipher_ints, d, n)
        except Exception:
            await ctx.send("Decryption failed. Double-check your `d`, `n`, and ciphertext values.")
            return

        await ctx.send(f"**RSA Decrypted:**\n`{plaintext}`")

    @commands.command(name="rsasolve")
    async def rsa_solve(self, ctx: commands.Context, p: int, q: int, e: int, *, message: str = None):
        """
        Solve RSA step-by-step given p, q, e. Optionally encrypt a message.
        Usage: $rsasolve <p> <q> <e> [message]
        Example: $rsasolve 61 53 17 HELLO
        """
        if not (is_prime(p) and is_prime(q)):
            await ctx.send("Both `p` and `q` must be prime numbers.")
            return
        if p == q:
            await ctx.send("`p` and `q` must be different primes.")
            return

        n   = p * q
        phi = (p - 1) * (q - 1)

        if gcd(e, phi) != 1:
            await ctx.send(f"`e = {e}` is not coprime with φ(n) = {phi}. Choose a different `e`.")
            return

        d = mod_inverse(e, phi)

        lines = [
            "**RSA Problem — Step by Step**",
            "```",
            f"p          = {p}",
            f"q          = {q}",
            f"n  = p × q = {n}",
            f"φ(n)       = (p-1)(q-1) = {phi}",
            f"e          = {e}",
            f"d  = e⁻¹ mod φ(n) = {d}",
            "```",
            f"**Public Key:**  (e=`{e}`, n=`{n}`)",
            f"**Private Key:** (d=`{d}`, n=`{n}`)",
        ]

        if message:
            if any(ord(c) >= n for c in message):
                lines.append("\nMessage characters exceed `n` — encryption skipped. Use larger primes.")
            else:
                cipher    = rsa_encrypt(message, e, n)
                decrypted = rsa_decrypt(cipher, d, n)
                cipher_str = " ".join(str(x) for x in cipher)

                lines += [
                    "",
                    f"**Encrypted:** `{cipher_str}`",
                    f"**Decrypted:** `{decrypted}`",
                    "",
                    "**Character breakdown:**",
                    "```",
                    f"{'Char':<6} {'ASCII':>6} {'Encrypted':>12}",
                    "-" * 28,
                ]
                for char, enc in zip(message, cipher):
                    lines.append(f"{char:<6} {ord(char):>6} {enc:>12}")
                lines.append("```")

        await ctx.send("\n".join(lines))

    @commands.command(name="rsainfo")
    async def rsa_info(self, ctx: commands.Context):
        """Explain how RSA works with a step-by-step embed. Usage: $rsainfo"""
        embed = discord.Embed(
            title="How RSA Works",
            color=discord.Color.blue()
        )
        embed.add_field(name="1. Key Generation", value=(
            "Pick two large primes **p** and **q**\n"
            "Compute **n = p × q**\n"
            "Compute **φ(n) = (p-1)(q-1)**\n"
            "Choose **e** coprime to φ(n) — usually `65537`\n"
            "Compute **d = e⁻¹ mod φ(n)**"
        ), inline=False)
        embed.add_field(name="2. Encryption", value=(
            "Public key = **(e, n)**\n"
            "Formula: **C = Mᵉ mod n**\n"
            "M = ASCII value of each character"
        ), inline=False)
        embed.add_field(name="3. Decryption", value=(
            "Private key = **(d, n)**\n"
            "Formula: **M = Cᵈ mod n**\n"
            "Result is converted back to characters"
        ), inline=False)
        embed.add_field(name="Bot Commands", value=(
            "`$rsagen [bits]` — Generate a keypair (256/512/1024)\n"
            "`$rsaencrypt <e> <n> <message>` — Encrypt a message\n"
            "`$rsadecrypt <d> <n> <integers>` — Decrypt ciphertext\n"
            "`$rsasolve <p> <q> <e> [message]` — Full step-by-step solve\n"
            "`$rsainfo` — This help page"
        ), inline=False)
        embed.set_footer(text="Security relies on the difficulty of factoring large n = p × q")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Crypto(bot))