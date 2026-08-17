#ifndef IRX_ANTICHEAT_H
#define IRX_ANTICHEAT_H

// Lightweight server-authoritative anomaly scoring. The surrounding server
// remains authoritative; clients only submit events that are validated here.

static int irx_ac_pickup_score[MAXCLIENTS] = { 0 };
static int irx_ac_shotgun_score[MAXCLIENTS] = { 0 };
static int irx_ac_last_notice[MAXCLIENTS] = { 0 };

inline void irx_ac_reset_client(int cn)
{
    if(cn >= 0 && cn < MAXCLIENTS)
    {
        irx_ac_pickup_score[cn] = 0;
        irx_ac_shotgun_score[cn] = 0;
        irx_ac_last_notice[cn] = 0;
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

inline void pickup_checks(client *c, float excess)
{
    if(!c || c->clientnum < 0 || c->clientnum >= MAXCLIENTS || excess <= 0.0f) return;
    const int cn = c->clientnum;
    int points = 1 + (int)(excess / 4.0f);
    if(points > 12) points = 12;
    irx_ac_pickup_score[cn] += points;
    if(irx_ac_pickup_score[cn] > 100) irx_ac_pickup_score[cn] = 100;
    irx_ac_notice(c, "pickup-distance", irx_ac_pickup_score[cn]);
}

// Called by serverevents.h for every shotgun hit. Reject impossible ray layouts
// before damage is applied. Existing server code already checks aggregate ray
// counts; this adds defensive per-field validation and a reusable AC hook.
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
        irx_ac_shotgun_score[cn] += 8;
        if(irx_ac_shotgun_score[cn] > 100) irx_ac_shotgun_score[cn] = 100;
        irx_ac_notice(actor, "shotgun-packet", irx_ac_shotgun_score[cn]);
        return false;
    }
    return true;
}

#endif
