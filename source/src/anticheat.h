#ifndef IRX_ANTICHEAT_H
#define IRX_ANTICHEAT_H

// Lightweight server-authoritative anomaly scoring. The surrounding server
// already decides whether a pickup distance is impossible; this hook records
// repeated severe violations without trusting any client-supplied verdict.

static int irx_ac_pickup_score[MAXCLIENTS] = { 0 };
static int irx_ac_last_notice[MAXCLIENTS] = { 0 };

inline void irx_ac_reset_client(int cn)
{
    if(cn >= 0 && cn < MAXCLIENTS)
    {
        irx_ac_pickup_score[cn] = 0;
        irx_ac_last_notice[cn] = 0;
    }
}

inline void pickup_checks(client *c, float excess)
{
    if(!c || c->clientnum < 0 || c->clientnum >= MAXCLIENTS || excess <= 0.0f) return;

    const int cn = c->clientnum;
    int points = 1 + (int)(excess / 4.0f);
    if(points > 12) points = 12;
    irx_ac_pickup_score[cn] += points;

    // High ping/jitter is already accounted for by check_pdist(). Still avoid
    // noisy enforcement and emit a server-side audit signal only after several
    // independent impossible pickup events.
    if(irx_ac_pickup_score[cn] >= 12 && servmillis - irx_ac_last_notice[cn] > 10000)
    {
        irx_ac_last_notice[cn] = servmillis;
        xlog(ACLOG_INFO,
             "[iRx AC] [%s] %s suspicious pickup distance: excess=%.2f score=%d ping=%d pj=%d",
             c->hostname, c->name, excess, irx_ac_pickup_score[cn], c->ping, c->spj);

        defformatstring(msg)("iRx anti-cheat flagged %s for server review (score %d)", c->name, irx_ac_pickup_score[cn]);
        sendservmsg(msg);
    }

    // Slowly cap the score so one ancient burst cannot grow without bound.
    if(irx_ac_pickup_score[cn] > 100) irx_ac_pickup_score[cn] = 100;
}

#endif
