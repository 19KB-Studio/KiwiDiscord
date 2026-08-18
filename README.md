# Discord Bot
A Discord bot that keeps your community updated with daily AI, tech, and cybersecurity news. KiwiBot also provides built in cryptography utilities, making it a useful companion for technology and cybersecurity enthusiasts.
## File Structure
```
main.py          — Entry point, loads all cogs
ai.py            — Openrouter and Tavily for web search ($ask)
crypto.py        — Encoding/decoding/hashing/RSA ($encode, $decode, $hash, $rsa*)
updates.py       — Daily tech/AI/cybersecurity news ($news, $setnewschannel)
moderation.py    — Nicknames, channel management, roleplay ($nick, $rp, $lock, ...)
ctf.py           — CTF event tracker cog ($setctfchannel, $ctfupcoming)
ctfclient.py     — CTFtime API client
db.py            — Neon Postgres access for the CTF tracker
filters.py       — Event filtering logic for the CTF tracker
formatting.py    — Discord embed formatting for CTF events
schema.sql       — Postgres schema for the CTF tracker (run once via psql)
server.py        — Flask keep-alive web server
requirements.txt — Python dependencies
```

### Crypto (`crypto.py`)
| Command | Description |
|---|---|
| `$hash <text>` | Generate MD5/SHA1/SHA256/SHA512 hashes |
| `$encode <text>` | Auto-encode (base64, hex, url) |
| `$encode base64\|hex\|url\|rot<N>\|xor <text>` | Encode with specific method |
| `$decode <text>` | Auto multi-layer decode |
| `$decode base64\|hex\|url\|rot<N>\|xor <text>` | Decode with specific method |
| `$rsagen [bits]` | Generate an RSA keypair (256 / 512 / 1024) |
| `$rsaencrypt <e> <n> <message>` | Encrypt with an RSA public key |
| `$rsadecrypt <d> <n> <ciphertext>` | Decrypt RSA ciphertext |
| `$rsasolve <p> <q> <e> [message]` | Step-by-step RSA solution |
| `$rsainfo` | Explain how RSA works |

### CTF Tracker (`ctf.py`)
| Command | Description |
|---|---|
| `$setctfchannel [yes\|no]` | Set this channel for CTF notifications (admin only); `yes/no` controls whether offline India events are included |
| `$ctfupcoming` | List upcoming CTFs matching your filters |

Requires `DATABASE_URL` (Neon Postgres) set in the environment, and the schema applied once via `psql "$DATABASE_URL" -f schema.sql`.

### Updates (`updates.py`)
| Command | Description |
|---|---|
| `$news [ai\|cybersecurity\|tech\|all]` | Fetch latest tech news |
| `$setnewschannel` | Set current channel for daily news (admin only) |

### Moderation (`moderation.py`)
| Command | Description |
|---|---|
| `$nick @user <name>` | Set member nickname |
| `$resetnick @user` | Reset member nickname |
| `$mynick <name>` | Set your own nickname |
| `$purge [amount]` | Delete last N messages (max 100) |
| `$slowmode [seconds]` | Set channel slowmode |
| `$lock` / `$unlock` | Lock or unlock the channel |
| `$createchannel text\|voice <name>` | Create a channel |
| `$deletechannel [#channel]` | Delete a channel (with confirmation) |
| `$rp [theme]` | Start a roleplay session |
| `$rpjoin` | Join active roleplay |
| `$rpsay <message>` | Speak in character |
| `$rpend` | End roleplay session |
| `$rpstatus` | Show session info |
