#!/usr/bin/env python3
from pathlib import Path
import argparse
import sys

MARK = "IRX_SHOOTER_PATCH_V3"


def replace(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"[ok] {label}: already applied")
        return
    if old not in text:
        raise RuntimeError(f"{label}: target not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"[ok] {label}")


def patch(root: Path):
    src = root / "source" / "src"

    # Network protocol: both desktop and mobile iRx Shooter builds use the same
    # incompatible protocol version, so legacy 1300 clients cannot misparse the
    # new smoke packets.
    p = src / "protocol.h"
    replace(p, "#define PROTOCOL_VERSION 1300", "#define PROTOCOL_VERSION 1301", "protocol version 1301")
    replace(
        p,
        "    SV_GETVITA, SV_VITADATA,\n    SV_NUM",
        "    SV_GETVITA, SV_VITADATA,\n    SV_IRX_SMOKE_THROW, SV_IRX_SMOKE,\n    SV_NUM",
        "smoke protocol ids",
    )

    p = src / "protocol.cpp"
    replace(
        p,
        "    SV_GETVITA, 2, SV_VITADATA, 0,\n    -1",
        "    SV_GETVITA, 2, SV_VITADATA, 0,\n    SV_IRX_SMOKE_THROW, 7, SV_IRX_SMOKE, 8,\n    -1",
        "smoke protocol sizes",
    )

    # Keep original map entity numeric IDs stable: smoke is appended after the
    # historical types rather than inserted into the pickup block.
    p = src / "entity.h"
    replace(
        p,
        "    DUMMYENT,                   // temporary entity without any function - will not be saved to map files, used to mark positions and for scripting\n    MAXENTTYPES",
        "    DUMMYENT,                   // temporary entity without any function - will not be saved to map files, used to mark positions and for scripting\n    I_SMOKE,                    // iRx Shooter smoke grenade pickup (appended to preserve legacy map IDs)\n    MAXENTTYPES",
        "smoke entity id",
    )
    replace(
        p,
        "#define isitem(i) ((i) >= I_CLIPS && (i) <= I_AKIMBO)",
        "#define isitem(i) (((i) >= I_CLIPS && (i) <= I_AKIMBO) || (i) == I_SMOKE)",
        "smoke pickup classification",
    )
    replace(
        p,
        "    int gunselect;\n    bool akimbo;\n    int ammo[NUMGUNS], mag[NUMGUNS], gunwait[NUMGUNS];",
        "    int gunselect;\n    bool akimbo;\n    int smokes;\n    int ammo[NUMGUNS], mag[NUMGUNS], gunwait[NUMGUNS];",
        "smoke inventory field",
    )
    replace(
        p,
        "    playerstate() : armour(0), primary(GUN_ASSAULT), nextprimary(GUN_ASSAULT), akimbo(false) {}",
        "    playerstate() : armour(0), primary(GUN_ASSAULT), nextprimary(GUN_ASSAULT), akimbo(false), smokes(0) {}",
        "smoke inventory init",
    )
    replace(
        p,
        "            case I_AKIMBO: return !akimbo;\n            default: return false;",
        "            case I_AKIMBO: return !akimbo;\n            case I_SMOKE: return smokes < 2;\n            default: return false;",
        "smoke canpickup",
    )
    replace(
        p,
        "            case I_AKIMBO:\n                akimbo = true;\n                mag[GUN_AKIMBO] = guns[GUN_AKIMBO].magsize;\n                additem(ammostats[GUN_AKIMBO], ammo[GUN_AKIMBO]);\n                break;\n        }",
        "            case I_AKIMBO:\n                akimbo = true;\n                mag[GUN_AKIMBO] = guns[GUN_AKIMBO].magsize;\n                additem(ammostats[GUN_AKIMBO], ammo[GUN_AKIMBO]);\n                break;\n            case I_SMOKE:\n                smokes = min(smokes + 1, 2);\n                break;\n        }",
        "smoke pickup inventory",
    )
    replace(
        p,
        "        akimbo = false;\n        loopi(NUMGUNS) ammo[i] = mag[i] = gunwait[i] = 0;",
        "        akimbo = false;\n        smokes = 0;\n        loopi(NUMGUNS) ammo[i] = mag[i] = gunwait[i] = 0;",
        "smoke respawn reset",
    )

    # Entity rendering: reuse the existing nade pickup model so no missing model
    # can make the new item invisible.
    p = src / "entities.cpp"
    replace(
        p,
        "    const char *mdlname = entmdlnames[isitem(e.type) && !(m_lss && e.type == I_GRENADE) ? e.type - I_CLIPS + 1 : 0];  // render double nades in lss",
        "    const char *mdlname = e.type == I_SMOKE ? \"pickups/nade\" : entmdlnames[isitem(e.type) && !(m_lss && e.type == I_GRENADE) ? e.type - I_CLIPS + 1 : 0];  // iRx smoke reuses nade pickup model",
        "smoke pickup model",
    )

    # Deterministically add smoke pickups next to a subset of grenade pickups.
    # Server and client use the same original entity count and rule, so the new
    # appended indices are identical without changing any shipped map file.
    p = src / "clientgame.cpp"
    replace(
        p,
        "    clearbounceents();\n    preparectf(!m_flags_);",
        "    clearbounceents();\n    {\n        bool hassmoke = false;\n        loopv(ents) if(ents[i].type == I_SMOKE) { hassmoke = true; break; }\n        if(!hassmoke && !m_noitems)\n        {\n            int originalents = ents.length();\n            loopi(originalents) if(ents[i].type == I_GRENADE && !(i & 1))\n            {\n                entity smoke = ents[i];\n                smoke.type = I_SMOKE;\n                smoke.x += 1;\n                smoke.spawned = true;\n                ents.add(smoke);\n            }\n        }\n    }\n    preparectf(!m_flags_);",
        "client smoke pickup injection",
    )

    # Confirmed kills get a short visual/audio burst. This runs only after the
    # authoritative kill message is processed, not on client-side hit guesses.
    replace(
        p,
        "    exechook(HOOK_SP, \"onKill\", \"%d %d %d %d\", act->clientnum, pl->clientnum, gun, gib ? 1 : 0);",
        "    exechook(HOOK_SP, \"onKill\", \"%d %d %d %d\", act->clientnum, pl->clientnum, gun, gib ? 1 : 0);\n\n    if(act == player1 && pl != player1 && !isteam(pl->team, act->team))\n    {\n        particle_splash(PART_SPARK, gib ? 120 : 72, gib ? 900 : 550, pl->o);\n        particle_splash(PART_SMOKE, gib ? 42 : 24, 900, pl->o);\n        if(gib) particle_fireball(PART_FIREBALL, pl->o);\n        audiomgr.playsound(gib ? S_HEADSHOT : S_HITSOUND, SP_HIGHEST);\n    }",
        "confirmed kill effect",
    )

    # Native smoke projectile and throw command. BT_NADE deliberately reuses the
    # mature bounce physics and grenade model, but destroy() has no damage path.
    p = src / "weapon.cpp"
    smoke_code = r'''

// IRX_SHOOTER_PATCH_V3: networked smoke grenade projectile.
VARP(irxsmokettl, 4000, 9500, 15000);
VARP(irxsmokedensity, 16, 44, 96);

class irxsmokeent : public bounceent
{
    bool emitted;
public:
    irxsmokeent(playerent *owner, const vec &from, const vec &velocity) : emitted(false)
    {
        this->owner = owner;
        millis = lastmillis;
        timetolive = 2200;
        bouncetype = BT_NADE;
        maxspeed = 30.0f;
        rotspeed = 6.0f;
        o = from;
        vel = velocity;
        inwater = waterlevel > o.z;
        resetinterp();
    }

    virtual void destroy()
    {
        if(emitted) return;
        emitted = true;
        audiomgr.playsound(S_GRENADEBOUNCE2, &o);
        loopi(7)
        {
            vec cloud(o);
            cloud.x += (rnd(81) - 40) / 20.0f;
            cloud.y += (rnd(81) - 40) / 20.0f;
            cloud.z += rnd(31) / 20.0f;
            particle_splash(PART_SMOKE, irxsmokedensity, irxsmokettl, cloud);
        }
    }

    virtual void oncollision()
    {
        audiomgr.playsound(rnd(2) ? S_GRENADEBOUNCE1 : S_GRENADEBOUNCE2, &o);
    }
};

void irx_spawn_smoke(int ownercn, const vec &from, const vec &velocity)
{
    playerent *owner = ownercn == player1->clientnum ? player1 : getclient(ownercn);
    if(!owner) return;
    bounceents.add(new irxsmokeent(owner, from, velocity));
}

void irxsmoke()
{
    if(!player1 || player1->state != CS_ALIVE || player1->smokes <= 0 || ispaused) return;
    float cp = cosf(RAD * player1->pitch);
    vec velocity(sinf(RAD * player1->yaw) * cp, -cosf(RAD * player1->yaw) * cp, sinf(RAD * player1->pitch));
    vec from(player1->o);
    from.add(vec(velocity).mul(1.15f));
    velocity.mul(1.55f);
    addmsg(SV_IRX_SMOKE_THROW, "ri6",
           int(from.x * DNF), int(from.y * DNF), int(from.z * DNF),
           int(velocity.x * DNF), int(velocity.y * DNF), int(velocity.z * DNF));
}
COMMAND(irxsmoke, "");
'''
    text = p.read_text(encoding="utf-8")
    if MARK not in text:
        anchor = "// gun base class\n"
        if anchor not in text:
            raise RuntimeError("native smoke projectile: anchor not found")
        p.write_text(text.replace(anchor, smoke_code + "\n" + anchor, 1), encoding="utf-8")
        print("[ok] native smoke projectile")
    else:
        print("[ok] native smoke projectile: already applied")

    # Client packet handler for the server-authoritative smoke broadcast.
    p = src / "clients2c.cpp"
    replace(
        p,
        "            case SV_SHOTFX:\n            {",
        "            case SV_IRX_SMOKE:\n            {\n                int ownercn = getint(p);\n                vec from, velocity;\n                loopk(3) from[k] = getint(p) / DNF;\n                loopk(3) velocity[k] = getint(p) / DNF;\n                if(ownercn == player1->clientnum && player1->smokes > 0) player1->smokes--;\n                extern void irx_spawn_smoke(int ownercn, const vec &from, const vec &velocity);\n                irx_spawn_smoke(ownercn, from, velocity);\n                break;\n            }\n\n            case SV_SHOTFX:\n            {",
        "client smoke packet",
    )

    # Dedicated server smoke pickup spawning and authoritative throw validation.
    p = src / "server.cpp"
    replace(
        p,
        "        case I_AKIMBO: sec = 60; break;",
        "        case I_AKIMBO: sec = 60; break;\n        case I_SMOKE: sec = 20; break;",
        "smoke respawn time",
    )
    replace(
        p,
        "            loopi(sg->curmap->numents)\n            {\n                e.type = sg->curmap->enttypes[i];\n                e.transformtype(sg->smode);\n                server_entity se = { e.type, false, false, false, 0, sg->curmap->entpos_x[i], sg->curmap->entpos_y[i] };\n                sg->sents.add(se);\n                if(e.fitsmode(sg->smode)) sg->sents[i].spawned = sg->sents[i].legalpickup = true;\n            }",
        "            loopi(sg->curmap->numents)\n            {\n                e.type = sg->curmap->enttypes[i];\n                e.transformtype(sg->smode);\n                server_entity se = { e.type, false, false, false, 0, sg->curmap->entpos_x[i], sg->curmap->entpos_y[i] };\n                sg->sents.add(se);\n                if(e.fitsmode(sg->smode)) sg->sents[i].spawned = sg->sents[i].legalpickup = true;\n            }\n            if(!m_noitems)\n            {\n                int originalents = sg->sents.length();\n                loopi(originalents) if(sg->sents[i].type == I_GRENADE && !(i & 1))\n                {\n                    server_entity smoke = { I_SMOKE, true, true, false, 0, short(sg->sents[i].x + 1), sg->sents[i].y };\n                    sg->sents.add(smoke);\n                }\n            }",
        "server smoke pickup injection",
    )
    smoke_server = r'''            case SV_IRX_SMOKE_THROW:
            {
                int sv[6];
                loopi(6) sv[i] = getint(p);
                if(cl->state.state != CS_ALIVE || cl->state.smokes <= 0) break;
                vec from(sv[0] / DNF, sv[1] / DNF, sv[2] / DNF);
                vec velocity(sv[3] / DNF, sv[4] / DNF, sv[5] / DNF);
#ifdef ACAC
                if(!irx_ac_validate_smoke(cl, from, velocity)) break;
#else
                if(from.dist(cl->state.o) > 6.0f || velocity.magnitude() > 3.0f) break;
#endif
                cl->state.smokes--;
                sendf(-1, 1, "ri8", SV_IRX_SMOKE, sender,
                      sv[0], sv[1], sv[2], sv[3], sv[4], sv[5]);
                break;
            }

'''
    text = p.read_text(encoding="utf-8")
    if "case SV_IRX_SMOKE_THROW:" not in text:
        anchor = "            case SV_SHOOT:\n            {"
        if anchor not in text:
            raise RuntimeError("server smoke parser: anchor not found")
        p.write_text(text.replace(anchor, smoke_server + anchor, 1), encoding="utf-8")
        print("[ok] server smoke parser")
    else:
        print("[ok] server smoke parser: already applied")

    # Debug names + entity editor names.
    p = src / "server.h"
    replace(
        p,
        "    \"SV_PAUSEMODE\"\n};",
        "    \"SV_PAUSEMODE\",\n    \"SV_GETVITA\", \"SV_VITADATA\",\n    \"SV_IRX_SMOKE_THROW\", \"SV_IRX_SMOKE\"\n};",
        "debug message names",
    )
    replace(
        p,
        "    \"mapmodel\", \"trigger\", \"ladder\", \"ctf-flag\", \"sound\", \"clip\", \"plclip\", \"dummy\", \"\"",
        "    \"mapmodel\", \"trigger\", \"ladder\", \"ctf-flag\", \"sound\", \"clip\", \"plclip\", \"dummy\", \"smoke\", \"\"",
        "smoke entity name",
    )

    # User-facing branding only. Compatibility strings such as DEMO_MAGIC are
    # deliberately not touched.
    p = src / "main.cpp"
    replace(p, 'MessageBox(NULL, msg, "AssaultCube fatal error",', 'MessageBox(NULL, msg, "iRx Shooter fatal error",', "Windows fatal dialog branding")
    replace(p, 'screen = SDL_CreateWindow("AssaultCube",', 'screen = SDL_CreateWindow("iRx Shooter",', "window title branding")

    print("iRx Shooter native patchset applied successfully")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    args = ap.parse_args()
    try:
        patch(args.root.resolve())
    except Exception as exc:
        print(f"patch failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
