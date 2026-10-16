/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "../../kernel/mars_net_validation.h"

int main(void)
{
	const char *bad[] = { NULL, "", "mars", "/", "/mars//x", "/mars/../x",
		"/mars/./x", "/mars/..", "/mars/.", "/mars/x|/etc", "/mars/x\\y", "/mars/\nx" };
	size_t i;
	char longpath[4097];

	for (i = 0; i < sizeof(bad) / sizeof(bad[0]); i++)
		assert(!mars_net_path_valid(bad[i], false));
	assert(mars_net_path_valid("/mars", false));
	assert(mars_net_path_valid("/mars/resource-foo/data-host", false));
	assert(mars_net_path_valid("/mars/resource-foo/", true));
	assert(!mars_net_path_valid("/mars/resource-foo/", false));
	memset(longpath, 'x', sizeof(longpath));
	longpath[0] = '/';
	longpath[sizeof(longpath) - 1] = 0;
	assert(!mars_net_path_valid(longpath, false));
	assert(mars_net_name_valid("peer-1.example"));
	assert(!mars_net_name_valid("../peer"));
	assert(!mars_net_name_valid(".."));
	assert(!mars_net_name_valid("peer/other"));
	assert(mars_net_link_mutable("/mars/resource-x/data-other", "host"));
	assert(!mars_net_link_mutable("/mars/resource-x/data-host", "host"));
	assert(!mars_net_link_mutable("/etc/escaped", "host"));
	assert(!mars_net_link_mutable("/mars/resource-x/log-0001-host", "host"));
	assert(!mars_net_link_mutable("/mars/resource-x", "host"));
	assert(mars_net_link_target_valid("/dev/mapper/backing"));
	assert(mars_net_link_target_valid("data-other@other"));
	assert(!mars_net_link_target_valid("/etc/passwd"));
	assert(!mars_net_link_target_valid("../outside"));
	assert(!mars_net_link_target_valid("a/../../outside"));
	puts("network path validation: PASS");
	return 0;
}
