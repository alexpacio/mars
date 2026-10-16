/*
 * MARS Long Distance Replication Software
 *
 * This file is part of MARS project: http://schoebel.github.io/mars/
 *
 * Copyright (C) 2010-2014 Thomas Schoebel-Theuer
 * Copyright (C) 2011-2014 1&1 Internet AG
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */


//#define BRICK_DEBUGGING
//#define MARS_DEBUGGING
//#define IO_DEBUGGING

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/string.h>

#include "strategy.h"
#include "../mars_net.h"
#include "../mars_net_validation.h"

static
char *_mars_translate_hostname(const char *name)
{
	char *res = brick_strdup(name);
	char *tmp;
	char *trans;

	for (tmp = res; *tmp; tmp++) {
		if (*tmp == ':') {
			*tmp = '\0';
			break;
		}
	}

	tmp = path_make("/mars/ips/ip-%s", res);
	if (unlikely(!tmp)) {
		goto done;
	}

	trans = ordered_readlink(tmp, NULL);
	if (trans && trans[0]) {
		MARS_DBG("'%s' => '%s'\n", tmp, trans);
		brick_string_free(res);
		res = trans;
	} else {
		MARS_DBG("no translation for '%s'\n", tmp);
		brick_string_free(trans);
	}
	brick_string_free(tmp);
done:
	return res;
}

int mars_send_dent_list(struct mars_global *global, struct mars_socket *sock)
{
	struct list_head *anchor;
	struct list_head *tmp;
	struct mars_dent *dent;
	int status = 0;

	down_read(&global->dent_mutex);
	anchor = &global->dent_anchor;
	for (tmp = anchor->next; tmp != anchor; tmp = tmp->next) {
		dent = container_of(tmp, struct mars_dent, dent_link);
		dent->d_proto = MARS_PROTO_LEVEL;
		status = mars_send_struct(sock, dent, mars_dent_meta, true);
		if (status < 0)
			break;
	}
	if (status >= 0) { // send EOR
		status = mars_send_struct(sock, NULL, mars_dent_meta, false);
	}
	up_read(&global->dent_mutex);
	return status;
}
EXPORT_SYMBOL_GPL(mars_send_dent_list);

/* A peer is a cluster administrator, not an unrestricted host filesystem
 * writer. Validate pulled metadata before it enters the local merge queue.
 */
static bool received_dent_valid(const struct mars_dent *dent)
{
	const struct kstat *st = &dent->new_stat;
	const char *leaf;

	if (!mars_net_path_valid(dent->d_path, false) ||
	    (strcmp(dent->d_path, "/mars") && strncmp(dent->d_path, "/mars/", 6)))
		return false;
	leaf = strrchr(dent->d_path, '/') + 1;
	if (!dent->d_name || strcmp(leaf, dent->d_name) ||
	    dent->d_proto < 0 || dent->d_proto > 255 ||
	    /* -1 is the legacy "no mapfree handle" size-correction sentinel. */
	    dent->d_corr_A < -1 || dent->d_corr_B < -1 || st->size < 0 ||
	    st->mtime.tv_sec < 0 || st->mtime.tv_nsec < 0 || st->mtime.tv_nsec >= NSEC_PER_SEC ||
	    st->ctime.tv_sec < 0 || st->ctime.tv_nsec < 0 || st->ctime.tv_nsec >= NSEC_PER_SEC)
		return false;
	/* Protected entries occur in ordinary full snapshots. The merge skips
	 * them rather than rejecting a legitimate peer's entire snapshot.
	 */
	if (S_ISLNK(st->mode) && mars_net_link_mutable(dent->d_path, my_id()) &&
	    !mars_net_link_target_valid(dent->new_link))
		return false;
	return true;
}

#define MARS_NET_DENT_MAX 65536
#define MARS_NET_DENT_BYTES_MAX (32 * 1024 * 1024)

int mars_recv_dent_list(struct mars_global *global, struct mars_socket *sock)
{
	struct list_head *anchor;
	size_t bytes = 0;
	unsigned int count = 0;
	int status;

	down_write(&global->dent_mutex);
	anchor = &global->dent_anchor;
	for (;;) {
		struct mars_dent *dent = brick_zmem_alloc(sizeof(struct mars_dent));
		struct list_head *hash_anchor;
		const struct meta *meta;
		unsigned int hash;

		if (!dent) {
			status = -ENOMEM;
			goto done;
		}
		INIT_LIST_HEAD(&dent->dent_link);
		INIT_LIST_HEAD(&dent->dent_hash_link);
		INIT_LIST_HEAD(&dent->brick_list);

		status = mars_recv_struct(sock, dent, mars_dent_meta);
		if (status <= 0) {
			mars_free_dent(NULL, dent);
			goto done;
		}
		bytes += sizeof(*dent);
		for (meta = mars_dent_meta; meta->field_name; meta++) {
			const char *value;

			if (meta->field_type != FIELD_STRING)
				continue;
			value = *(char **)((char *)dent + meta->field_offset);
			if (value)
				bytes += strlen(value) + 1;
		}
		if (++count > MARS_NET_DENT_MAX || bytes > MARS_NET_DENT_BYTES_MAX ||
		    !received_dent_valid(dent)) {
			MARS_WRN("rejecting invalid or oversized peer metadata list\n");
			mars_free_dent(NULL, dent);
			status = -EPROTO;
			goto done;
		}
		if (dent->d_proto >= 2) {
			sock->s_remote_proto_level = dent->d_proto;
			sock->s_common_proto_level = min(dent->d_proto, MARS_PROTO_LEVEL);
		}
		list_add_tail(&dent->dent_link, anchor);
		if (!dent->d_path || dent->d_unordered)
			continue;
		hash = dent_hash(dent->d_path, strlen(dent->d_path));
		dent->d_hash = hash;
		hash_anchor = DENT_HASH_ANCHOR(global, hash);
		list_add_tail(&dent->dent_hash_link, hash_anchor);
	}
done:
	up_write(&global->dent_mutex);
	return status;
}
EXPORT_SYMBOL_GPL(mars_recv_dent_list);


////////////////// module init stuff /////////////////////////


int __init init_sy_net(void)
{
	MARS_INF("init_sy_net()\n");
	mars_translate_hostname = _mars_translate_hostname;
	return 0;
}

void exit_sy_net(void)
{
	MARS_INF("exit_sy_net()\n");
}
