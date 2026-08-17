#ifndef IRX_ANTICHEAT_H
#define IRX_ANTICHEAT_H

// iRx Shooter server-authoritative anomaly scoring. Clients submit intent and
// observations only; inventory, cadence and impossible-state decisions remain
// server side. This intentionally avoids invasive client scanning/rootkit-like
// behaviour while hardening gameplay-critical packet validation.

static int irx_ac_pickup_score[MAXCLIENTS] = { 0 };
static int irx_ac_shotgun_score[MAXCLIENTS] = { 0 };
static int irx_ac_smoke_score[MAXCLIENTS] = { 0 };
static int irx_ac_last_notice[MAXCLIENTS] = { 0 };
static int irx_ac_last_smoke[MAXCLIENTS] = { 0 };

inline void irx_ac_reset_client(int cn)
{
    if(cn >= 0 && cn < MAXCLIENTS)
    {
        irx_ac_pickup_score[cn] = 0;
        irx_ac_shotgun_score[cn] = 0;
        irx_ac_smoke_score[cn] = 0;
        irx_ac_last_notice[cn] = 0;
        irx_ac_last_smoke[cn] = 0;
    }
}

inline void irx_ac_notice(client *c, const char *kind, int score)
{
    if(!c || c->clientnum < 0 || c->clientnum >= MAXCLIENTS) return;
    const int cn = c->clientnum;
    if(score < 12 || servmillis - irx_ac_last_notice[cn] <= 10000) return;
    irx_ac_last_notice[cn] = servmillis;
    xlog(ACLOG_INFO, "[iRx AC] [%s] %s anomaly=%s score=%d ping=%d pj=%d",
         c->hostname, c->name, kind, score, c->ping, c->spj);
    defformatstring(msg)("iRx anti-cheat flagged %s for server review (%s:%d)", c->name, kind, score);
    sendservmsg(msg);
}

inline void irx_ac_addscore(client *c, int &score, const char *kind, int points)
{
    if(!c || c->clientnum < 0 || c->clientnum >= MAXCLIENTS) return;
    score += points;
    if(score > 100) score = 100;
    irx_ac_notice(c, kind, score);
}

inline void pickup_checks(client *c, float excess)
{
    if(!c || c->clientnum < 0 || c->clientnum >= MAXCLIENTS || excess <= 0.0f) return;
    const int cn = c->clientnum;
    int points = 1 + (int)(excess / 4.0f);
    if(points > 12) points = 12;
    irx_ac_addscore(c, irx_ac_pickup_score[cn], "pickup-distance", points);
}

// Called by serverevents.h for every shotgun hit. Reject impossible ray layouts
// before damage is applied. Existing server code also checks aggregate counts.
inline bool sg_engine(client *target, client *actor, int numhits_c, int numhits_m, int numhits_o, int bonusdist)
{
    if(!target || !actor) return false;
    if(actor->clientnum < 0 || actor->clientnum >= MAXCLIENTS) return false;
    if(numhits_c < 0 || numhits_m < 0 || numhits_o < 0) return false;
    if(numhits_c > SGRAYS || numhits_m > SGRAYS || numhits_o > SGRAYS) return false;
    const int rays = numhits_c + numhits_m + numhits_o;
    if(rays < 1 || rays > 3 * SGRAYS || bonusdist < 0 || bonusdist > SGDMGBONUS)
    {
        const int cn = actor->clientnum;
        irx_ac_addscore(actor, irx_ac_shotgun_score[cn], "shotgun-packet", 8);
        return false;
    }
    return true;
}

// Validate the new smoke action entirely against authoritative server state:
// possession is checked by the packet handler; here we enforce origin, velocity
// and cadence. Jitter never needs to expand these limits because a smoke throw
// is not rewound hit detection.
inline bool irx_ac_validate_smoke(client *actor, const vec &from, const vec &velocity)
{
    if(!actor || actor->clientnum < 0 || actor->clientnum >= MAXCLIENTS) return false;
    const int cn = actor->clientnum;
    bool valid = true;
    int points = 0;

    float odist = from.dist(actor->state.o);
    float speed = velocity.magnitude();
    if(odist > 6.0f) { valid = false; points += odist > 12.0f ? 12 : 6; }
    if(speed < 0.05f || speed > 3.0f) { valid = false; points += 8; }
    if(irx_ac_last_smoke[cn] && servmillis - irx_ac_last_smoke[cn] < 350)
    {
        valid = false;
        points += 10;
    }

    if(!valid)
    {
        irx_ac_addscore(actor, irx_ac_smoke_score[cn], "smoke-packet", points ? points : 4);
        return false;
    }
    irx_ac_last_smoke[cn] = servmillis;
    return true;
}

#endif
