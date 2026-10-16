/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef MARS_NET_VALIDATION_H
#define MARS_NET_VALIDATION_H

/* Shared with the userspace regression test.  This validates syntax only;
 * VFS mutation helpers must also prohibit symlinks while resolving parents.
 */
static inline bool mars_net_components_valid(const char *path, bool directory, bool absolute)
{
	const char *part, *p;
	size_t len;

	if (!path || (absolute && path[0] != '/'))
		return false;
	len = strnlen(path, 4096);
	if (!len || len >= 4096)
		return false;
	part = path + absolute;
	for (p = part; ; p++) {
		if (*p && ((unsigned char)*p < 32 || *p == '|' || *p == '\\'))
			return false;
		if (*p && *p != '/')
			continue;
		len = p - part;
		if (!len || len > 255 || (len == 1 && part[0] == '.') ||
		    (len == 2 && part[0] == '.' && part[1] == '.'))
			return false;
		if (!*p)
			return true;
		if (!p[1])
			return directory;
		part = p + 1;
	}
}

static inline bool mars_net_path_valid(const char *path, bool directory)
{
	return mars_net_components_valid(path, directory, true);
}

static inline bool mars_net_name_valid(const char *name)
{
	const unsigned char *p = (const unsigned char *)name;

	if (!p || !*p || strlen(name) > 255 || !strcmp(name, ".") || !strcmp(name, ".."))
		return false;
	for (; *p; p++)
		if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
		      (*p >= '0' && *p <= '9') || *p == '-' || *p == '_' || *p == '.'))
			return false;
	return true;
}

/* Shared by direct PUSH requests and metadata pulled from another node. */
static inline bool mars_net_link_mutable(const char *path, const char *local_id)
{
	const char *leaf;

	if (!mars_net_path_valid(path, false) || strncmp(path, "/mars/", 6))
		return false;
	leaf = strrchr(path, '/') + 1;
	return *leaf != '.' && strncmp(leaf, "resource-", 9) &&
		strncmp(leaf, "todo-", 5) && strncmp(leaf, "actual-", 7) &&
		strncmp(leaf, "defaults", 8) && strncmp(leaf, "local-", 6) &&
		strcmp(leaf, "userspace") && strcmp(leaf, "ips") &&
		strncmp(leaf, "log-", 4) && strncmp(leaf, "_file-", 6) &&
		(strncmp(leaf, "data-", 5) || strcmp(leaf + 5, local_id));
}

static inline bool mars_net_link_target_valid(const char *target)
{
	if (!target || !*target)
		return false;
	if (*target == '/')
		return mars_net_path_valid(target, false) &&
			(!strncmp(target, "/mars/", 6) || !strncmp(target, "/dev/", 5));
	if (!strchr(target, '/'))
		return strcmp(target, ".") && strcmp(target, "..");
	return mars_net_components_valid(target, false, false);
}
#endif
