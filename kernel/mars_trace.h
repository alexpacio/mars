/* SPDX-License-Identifier: GPL-2.0-or-later */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM mars

#if !defined(_MARS_TRACE_H) || defined(TRACE_HEADER_MULTI_READ)
#define _MARS_TRACE_H

#include <linux/string.h>
#include <linux/tracepoint.h>
#include <linux/types.h>

/* Store strings in the ring buffer: brick and peer objects may disappear
 * before a consumer reads an event.  Dynamic arrays also support kernels
 * before and after the __assign_str() API change.
 */
#define MARS_TRACE_TEXT(text) ((text) ? (text) : "")
#define MARS_TRACE_COPY(field, text) \
	memcpy(__get_str(field), MARS_TRACE_TEXT(text), \
	       strlen(MARS_TRACE_TEXT(text)) + 1)

TRACE_EVENT(mars_request,
	TP_PROTO(const char *resource, const char *phase, const void *request,
		 loff_t pos, unsigned int len, u32 flags, int error),
	TP_ARGS(resource, phase, request, pos, len, flags, error),
	TP_STRUCT__entry(
		__dynamic_array(char, resource, strlen(MARS_TRACE_TEXT(resource)) + 1)
		__dynamic_array(char, phase, strlen(MARS_TRACE_TEXT(phase)) + 1)
		__field(const void *, request)
		__field(loff_t, pos)
		__field(unsigned int, len)
		__field(u32, flags)
		__field(int, error)
	),
	TP_fast_assign(
		MARS_TRACE_COPY(resource, resource);
		MARS_TRACE_COPY(phase, phase);
		__entry->request = request;
		__entry->pos = pos;
		__entry->len = len;
		__entry->flags = flags;
		__entry->error = error;
	),
	TP_printk("resource=%s phase=%s request=%p pos=%lld len=%u flags=0x%x error=%d",
		  __get_str(resource), __get_str(phase), __entry->request,
		  (long long)__entry->pos, __entry->len, __entry->flags, __entry->error)
);

TRACE_EVENT(mars_log,
	TP_PROTO(const char *resource, const char *phase, loff_t pos,
		 unsigned int len, int error),
	TP_ARGS(resource, phase, pos, len, error),
	TP_STRUCT__entry(
		__dynamic_array(char, resource, strlen(MARS_TRACE_TEXT(resource)) + 1)
		__dynamic_array(char, phase, strlen(MARS_TRACE_TEXT(phase)) + 1)
		__field(loff_t, pos)
		__field(unsigned int, len)
		__field(int, error)
	),
	TP_fast_assign(
		MARS_TRACE_COPY(resource, resource);
		MARS_TRACE_COPY(phase, phase);
		__entry->pos = pos;
		__entry->len = len;
		__entry->error = error;
	),
	TP_printk("resource=%s phase=%s pos=%lld len=%u error=%d",
		  __get_str(resource), __get_str(phase), (long long)__entry->pos,
		  __entry->len, __entry->error)
);

TRACE_EVENT(mars_connection,
	TP_PROTO(const char *resource, const char *peer, unsigned int channel,
		 int state, int error),
	TP_ARGS(resource, peer, channel, state, error),
	TP_STRUCT__entry(
		__dynamic_array(char, resource, strlen(MARS_TRACE_TEXT(resource)) + 1)
		__dynamic_array(char, peer, strlen(MARS_TRACE_TEXT(peer)) + 1)
		__field(unsigned int, channel)
		__field(int, state)
		__field(int, error)
	),
	TP_fast_assign(
		MARS_TRACE_COPY(resource, resource);
		MARS_TRACE_COPY(peer, peer);
		__entry->channel = channel;
		__entry->state = state;
		__entry->error = error;
	),
	TP_printk("resource=%s peer=%s channel=%u state=%d error=%d",
		  __get_str(resource), __get_str(peer), __entry->channel,
		  __entry->state, __entry->error)
);

TRACE_EVENT(mars_role,
	TP_PROTO(const char *resource, bool primary),
	TP_ARGS(resource, primary),
	TP_STRUCT__entry(
		__dynamic_array(char, resource, strlen(MARS_TRACE_TEXT(resource)) + 1)
		__field(bool, primary)
	),
	TP_fast_assign(
		MARS_TRACE_COPY(resource, resource);
		__entry->primary = primary;
	),
	TP_printk("resource=%s primary=%d", __get_str(resource), __entry->primary)
);

#endif /* _MARS_TRACE_H */

#undef TRACE_INCLUDE_PATH
#define TRACE_INCLUDE_PATH .
#undef TRACE_INCLUDE_FILE
#define TRACE_INCLUDE_FILE mars_trace
#include <trace/define_trace.h>
