#!/usr/bin/env python
import re
import sys
import os

from collections import defaultdict
from json import loads
from time import time
from traceback import print_exc


class ExtractStatistics:

    def __init__(self, node_directory, handlers=[]):
        self.node_directory = node_directory
        self.handlers = handlers
        self.start_of_experiment = 0

    def add_handler(self, handler):
        self.handlers.append(handler)

    def parse(self):
        for handler in self.handlers:
            handler.parse(self)

        files = sorted(self.yield_files())
        if not len(files):
            print >> sys.stderr, "No files found to parse!"
            sys.exit(1)

        print >> sys.stderr, "Starting to parse", len(files), "files"

        self.min_timeoffset = sys.maxsize
        self.max_timeoffset = 0
        self.start_of_experiment = int(self.get_first_datetime(files))

        start_time = time()
        start_size = 0
        after_size = 0
        total_size = len(files)

        for node_nr, filename, outputdir in files:
            for handler in self.handlers:
                handler.new_file(node_nr, filename, outputdir)

            for line_nr, timestamp, timeoffset, key, json in self.read(filename):
                converted_json = None

                # we limit the output granularity to int
                timestamp = int(timestamp)
                timeoffset = int(timeoffset)

                for handler in self.handlers:
                    if handler.filter_line(node_nr, line_nr, timestamp, timeoffset, key):
                        if not converted_json:
                            converted_json = loads(json)

                        handler.handle_line(node_nr, line_nr, timestamp, timeoffset, key, converted_json)

                self.min_timeoffset = min(self.min_timeoffset, timeoffset)
                self.max_timeoffset = max(self.max_timeoffset, timeoffset)

            for handler in self.handlers:
                handler.end_file(node_nr, timestamp, timeoffset)

            after_size += 1

            diff_time = time() - start_time
            if diff_time > 15:
                diff_size = after_size - start_size
                remaining_size = total_size - after_size

                if diff_size > 0:
                    avg_proc_time = diff_time / diff_size
                    remaining_time = remaining_size * avg_proc_time
                    print >> sys.stderr, remaining_size, "files or", self.elapsed_time(remaining_time), "remaining (average", str(round(avg_proc_time, 2)) + 's)'
                else:
                    print >> sys.stderr, remaining_size, "files or ? remaining"

                start_time = time()
                start_size = after_size

        print >> sys.stderr, "All files parsed, merging..."

        for handler in self.handlers:
            handler.all_files_done(self)

        print >> sys.stderr, "Finished merging"

        print "XMIN=%d" % self.min_timeoffset
        print "XMAX=%d" % self.max_timeoffset
        print "XSTART=%d" % self.start_of_experiment

    def read(self, filename, filterkey=[]):
        for line_nr, line in enumerate(open(filename)):
            timestamp, _, key, json = line.split(' ', 3)

            if not filterkey or key in filterkey:
                timestamp = float(timestamp)
                timeoffset = timestamp - self.start_of_experiment
                yield line_nr, timestamp, timeoffset, key, json

    def read_last(self, filename, chars):
        # From http://stackoverflow.com/a/260352
        f = open(filename, "r")
        f.seek(0, 2)  # Seek @ EOF
        fsize = f.tell()  # Get Size
        f.seek(max(fsize - chars, 0), 0)  # Set pos @ last n chars

        # skip broken line
        f.readline()

        lines = f.readlines()
        lines.reverse()

        for line_nr, line in enumerate(lines):
            timestamp, _, key, json = line.split(' ', 3)

            timestamp = float(timestamp)
            timeoffset = timestamp - self.start_of_experiment

            yield -line_nr, timestamp, timeoffset, key, json

    def get_first_datetime(self, files):
        datetimes = []
        for node_nr, filename, outputdir in files:
            try:
                line_nr, timestamp, timeoffset, key, json = self.read(filename, ['annotate', ]).next()
                if json.strip() == "start-experiment":
                    datetimes.append(timestamp)
            except StopIteration:
                pass
            except:
                print_exc()

        # Fallback to old method
        if not datetimes:
            print >> sys.stderr, "Using fallback for get_first_datetime"
            for node_nr, filename, outputdir in files:
                line_nr, timestamp, timeoffset, key, json = self.read(filename).next()
                datetimes.append(timestamp)

        return min(datetimes)

    def yield_files(self):
        pattern = re.compile('[0-9]+')
        for headnode in os.listdir(self.node_directory):
            headdir = os.path.join(self.node_directory, headnode)
            if os.path.isdir(headdir):
                for node in os.listdir(headdir):
                    nodedir = os.path.join(self.node_directory, headnode, node)
                    if os.path.isdir(nodedir):
                        for peer in os.listdir(nodedir):
                            peerdir = os.path.join(self.node_directory, headnode, node, peer)
                            if os.path.isdir(peerdir) and pattern.match(peer):
                                peer_nr = int(peer)

                                filename = os.path.join(self.node_directory, headnode, node, peer, 'statistics.log')
                                if os.path.exists(filename):
                                    yield peer_nr, filename, peerdir

    def merge_records(self, inputfilename, outputfilename, columnindex, diffoutputfilename=None):
        all_nodes = []

        sum_records = {}
        for node_nr, _, inputdir in self.yield_files():
            all_nodes.append(node_nr)

            h_records = open(os.path.join(inputdir, inputfilename))
            for line in h_records:
                if line[0] == "#":
                    continue

                parts = line.split()
                if len(parts) > columnindex:
                    time = float(parts[1])
                    record = float(parts[columnindex])
                    if record == 0:
                        continue
                    sum_records.setdefault(time, {})[node_nr] = record
            h_records.close()

        diffoutputfile = os.path.join(self.node_directory, diffoutputfilename) if diffoutputfilename else None
        self.write_records(all_nodes, sum_records, os.path.join(self.node_directory, outputfilename), diffoutputfile)

    def write_records(self, all_nodes, sum_records, outputfile, diffoutputfile=None):
        if len(sum_records) > 0:
            all_nodes.sort()

            fp = open(outputfile, 'wb')
            fp2 = open(diffoutputfile, 'wb') if diffoutputfile else None

            print >> fp, 'time', ' '.join(map(str, all_nodes))
            if fp2:
                print >> fp2, 'time', ' '.join(map(str, all_nodes))

            prev_records = {}
            for time in sorted(sum_records.iterkeys()):
                print >> fp, time,
                if fp2:
                    print >> fp2, time,

                nodes = sum_records[time]
                for node in all_nodes:
                    value = nodes.get(node, prev_records.get(node, 0))
                    print >> fp, value,

                    if fp2:
                        diff = value - prev_records.get(node, 0)
                        print >> fp2, diff,

                    prev_records[node] = value
                print >> fp, ''
                if fp2:
                    print >> fp2, ''

            fp.close()
            if fp2:
                fp2.close()

    # From http://snipplr.com/view/5713/python-elapsedtime-human-readable-time-span-given-total-seconds/
    def elapsed_time(self, seconds, suffixes=['y', 'w', 'd', 'h', 'm', 's'], add_s=False, separator=' '):
        """
        Takes an amount of seconds and turns it into a human-readable amount of time.
        """
        # the formatted time string to be returned
        time = []

        # the pieces of time to iterate over (days, hours, minutes, etc)
        # - the first piece in each tuple is the suffix (d, h, w)
        # - the second piece is the length in seconds (a day is 60s * 60m * 24h)
        parts = [(suffixes[3], 60 * 60),
                 (suffixes[4], 60),
                 (suffixes[5], 1)]

        # for each time piece, grab the value and remaining seconds, and add it to
        # the time string
        for suffix, length in parts:
            value = int(seconds / length)
            if value > 0:
                seconds = seconds % length
                time.append('%s%s' % (str(value),
                           (suffix, (suffix, suffix + 's')[value > 1])[add_s]))

            if seconds < 1:
                if len(time) == 0:
                    time.append('<1s')
                break

        return separator.join(time)


class AbstractHandler(object):

    def parse(self, extract_statistics):
        pass

    def new_file(self, node_nr, filename, outputdir):
        pass

    def end_file(self, node_nr, timestamp, timeoffset):
        pass

    def all_files_done(self, extract_statistics):
        pass

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return True

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        pass


class BasicExtractor(AbstractHandler):

    def __init__(self):
        AbstractHandler.__init__(self)

        self.dispersy_in_out = defaultdict(lambda: [0, 0, 0, 0])
        self.nr_connections = []

    def parse(self, extract_statistics):
        # determine communities
        communities = set()
        for node_nr, filename, outputdir in extract_statistics.yield_files():
            try:
                for line_nr, timestamp, timeoffset, key, json in extract_statistics.read_last(filename, 2048):
                    if 'statistics' == key:
                        json = loads(json)

                        if json.get('communities'):
                            for community in json['communities']:
                                if community['nr_candidates']:
                                    communities.add(community['cid'])
            except:
                print_exc()

        self.communities = list(communities)
        self.communities.sort()

    def new_file(self, node_nr, filename, outputdir):
        node = str(node_nr)

        self.c_dropped_record = 0
        self.c_communities = defaultdict(lambda: 0)
        self.c_blstats = defaultdict(lambda: [0, 0, 0])

        self.max_incomming_connections = 0

        self.h_stat = open(os.path.join(outputdir, "stat.txt"), "w+")
        print >> self.h_stat, "# timestamp timeoffset total-send total-received"
        print >> self.h_stat, "0 0 0 0"

        self.h_drop = open(os.path.join(outputdir, "drop.txt"), "w+")
        print >> self.h_drop, "# timestamp timeoffset num-drops"
        print >> self.h_drop, "0 0 0"

        self.h_total_connections = open(os.path.join(outputdir, "total_connections.txt"), "w+")
        print >> self.h_total_connections, "# timestamp timeoffset (num-connections +)"
        print >> self.h_total_connections, "#", " ".join(self.communities)
        print >> self.h_total_connections, "0 0" + (" 0" * len(self.communities))

        self.h_blstats = open(os.path.join(outputdir, "bl_stat.txt"), "w+")
        print >> self.h_blstats, "# timestamp timeoffset (bl-skip +) (bl-reuse +) (bl-new +)"
        print >> self.h_blstats, "#", " ".join(self.communities)
        print >> self.h_blstats, "0 0" + (" 0 0" * len(self.communities))

    def end_file(self, node_nr, timestamp, timeoffset):
        print >> self.h_drop, timestamp, timeoffset, self.c_dropped_record

        print >> self.h_total_connections, timestamp, timeoffset,
        for community in self.communities:
            print >> self.h_total_connections, self.c_communities[community],
        print >> self.h_total_connections, ''

        self.nr_connections.append((self.max_incomming_connections, node_nr))
        self.nr_connections.sort(reverse=True)
        self.nr_connections = self.nr_connections[:10]

        self.h_stat.close()
        self.h_drop.close()
        self.h_total_connections.close()
        self.h_blstats.close()

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "statistics"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, value):
        self.dispersy_in_out[node_nr][0] = value.get("total_down", self.dispersy_in_out[node_nr][0])
        self.dispersy_in_out[node_nr][1] = value.get("total_up", self.dispersy_in_out[node_nr][1])
        self.dispersy_in_out[node_nr][2] = value.get("total_send", self.dispersy_in_out[node_nr][2])
        self.dispersy_in_out[node_nr][3] = value.get("received_count", self.dispersy_in_out[node_nr][3])

        if "total_down" in value or "total_up" in value:
            print >> self.h_stat, timestamp, timeoffset, self.dispersy_in_out[node_nr][1], self.dispersy_in_out[node_nr][0]

        if "drop_count" in value:
            self.c_dropped_record = value["drop_count"]
            print >> self.h_drop, timestamp, timeoffset, self.c_dropped_record

        if 'communities' in value:
            for community in value['communities']:
                if community.get('nr_candidates'):
                    self.c_communities[community['cid']] = community.get('nr_candidates')

                self.c_blstats[community['cid']][0] = community.get('sync_bloom_reuse', self.c_blstats[community['cid']][0])
                self.c_blstats[community['cid']][1] = community.get('sync_bloom_skip', self.c_blstats[community['cid']][1])
                self.c_blstats[community['cid']][2] = community.get('sync_bloom_new', self.c_blstats[community['cid']][2])

            if self.c_communities:
                self.max_incomming_connections = max(self.max_incomming_connections, max(self.c_communities.values()))

            print >> self.h_total_connections, timestamp, timeoffset,
            for community in self.communities:
                print >> self.h_total_connections, self.c_communities[community],
            print >> self.h_total_connections, ''

            print >> self.h_blstats, timestamp, timeoffset,
            for community in self.communities:
                print >> self.h_blstats, self.c_blstats[community][0],
            for community in self.communities:
                print >> self.h_blstats, self.c_blstats[community][1],
            for community in self.communities:
                print >> self.h_blstats, self.c_blstats[community][2],
            print >> self.h_blstats, ''

    def all_files_done(self, extract_statistics):
        f = open(os.path.join(extract_statistics.node_directory, "dispersy_incomming_connections.txt"), 'w')
        print >> f, "# peer max_incomming_connections"
        for nr, node in self.nr_connections:
            print >> f, node, nr
        f.close()

        extract_statistics.merge_records("total_record.txt", 'sum_total_records.txt', 2)
        extract_statistics.merge_records("stat.txt", 'send.txt', 2, 'send_diff.txt')
        extract_statistics.merge_records("stat.txt", 'received.txt', 3, 'received_diff.txt')
        extract_statistics.merge_records("drop.txt", 'dropped.txt', 2, 'dropped_diff.txt')

        for column in xrange(len(self.communities)):
            extract_statistics.merge_records("total_connections.txt", 'total_connections_%d.txt' % (column + 1), 2 + column)

            extract_statistics.merge_records("bl_stat.txt", 'bl_reuse_%d.txt' % (column + 1), 2 + column)
            extract_statistics.merge_records("bl_stat.txt", 'bl_skip_%d.txt' % (column + 1), 2 + len(self.communities) + column)
            extract_statistics.merge_records("bl_stat.txt", 'bl_new_%d.txt' % (column + 1), 2 + len(self.communities) + len(self.communities) + column)


class SuccMessages(AbstractHandler):

    def __init__(self, messages_to_plot):
        AbstractHandler.__init__(self)

        self.messages_to_plot = [message for message in messages_to_plot.split(',') if message.strip()]

    def new_file(self, node_nr, filename, outputdir):
        self.dispersy_msg_distribution = {}
        self.c_received_records = {}
        self.c_created_records = {}

        self.h_received_record = open(os.path.join(outputdir, "received-record.txt"), "w+")
        print >> self.h_received_record, "# timestamp timeoffset num-records"
        print >> self.h_received_record, "0 0 0"

        self.h_created_record = open(os.path.join(outputdir, "created-record.txt"), "w+")
        print >> self.h_created_record, "# timestamp timeoffset num-records"
        print >> self.h_created_record, "0 0 0"

        self.h_total_record = open(os.path.join(outputdir, "total_record.txt"), "w+")
        print >> self.h_total_record, "# timestamp timeoffset num-records"
        print >> self.h_total_record, "0 0 0"

    def end_file(self, node_nr, timestamp, timeoffset):
        c_received_record = sum(self.c_received_records.itervalues())
        c_created_record = sum(self.c_created_records.itervalues())

        print >> self.h_received_record, timestamp, timeoffset, c_received_record
        print >> self.h_created_record, timestamp, timeoffset, c_created_record
        print >> self.h_total_record, timestamp, timeoffset, c_received_record + c_created_record

        self.h_received_record.close()
        self.h_created_record.close()
        self.h_total_record.close()

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "statistics-successful-messages" or key == "statistics-created-messages"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        writeTotal = False

        if key == "statistics-successful-messages":
            for key, value in json.iteritems():
                self.dispersy_msg_distribution[key] = max((value, node_nr), self.dispersy_msg_distribution.get(key, (0, node_nr)))

                if not self.messages_to_plot or key in self.messages_to_plot:
                    self.c_received_records[key] = value
                    writeTotal = True

            if writeTotal:
                c_received_record = sum(self.c_received_records.itervalues())
                print >> self.h_received_record, timestamp, timeoffset, c_received_record

        elif key == "statistics-created-messages":
            for key, value in json.iteritems():
                if not self.messages_to_plot or key in self.messages_to_plot:
                    self.c_created_records[key] = value
                    writeTotal = True

            if writeTotal:
                c_created_record = sum(self.c_created_records.itervalues())
                print >> self.h_created_record, timestamp, timeoffset, c_created_record

        if writeTotal:
            c_received_record = sum(self.c_received_records.itervalues())
            c_created_record = sum(self.c_created_records.itervalues())
            print >> self.h_total_record, timestamp, timeoffset, c_received_record + c_created_record

    def all_files_done(self, extract_statistics):
        h_dispersy_msg_distribution = open(os.path.join(extract_statistics.node_directory, "dispersy-msg-distribution.txt"), "w+")
        print >> h_dispersy_msg_distribution, "# msg_name count"
        for msg, count in self.dispersy_msg_distribution.iteritems():
            print >> h_dispersy_msg_distribution, "%s %d %s" % (msg, count[0], count[1])
        h_dispersy_msg_distribution.close()


class StatisticMessages(AbstractHandler):

    def new_file(self, node_nr, filename, outputdir):
        self.h_statistics = open(os.path.join(outputdir, "scenario-statistics.txt"), "w+")
        print >> self.h_statistics, "# timestamp timeoffset key value"

    def end_file(self, node_nr, timestamp, timeoffset):
        self.h_statistics.close()

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "scenario-statistics"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        for key, value in json.iteritems():
            print >> self.h_statistics, time, timeoffset, key, value


class DropMessages(AbstractHandler):

    def __init__(self):
        AbstractHandler.__init__(self)
        self.dispersy_dropped_msg_distribution = {}

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "statistics-dropped-messages"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        for key, value in json.iteritems():
            self.dispersy_dropped_msg_distribution[key] = max((value, node_nr), self.dispersy_dropped_msg_distribution.get(key, (0, node_nr)))

    def all_files_done(self, extract_statistics):
        h_dispersy_dropped_msg_distribution = open(os.path.join(extract_statistics.node_directory, "dispersy-dropped-msg-distribution.txt"), "w+")

        print >> h_dispersy_dropped_msg_distribution, "# msg_name count"
        for msg, count in self.dispersy_dropped_msg_distribution.iteritems():
            print >> h_dispersy_dropped_msg_distribution, "%s %d %s" % (msg, count[0], count[1])
        h_dispersy_dropped_msg_distribution.close()


class BootstrapMessages(AbstractHandler):

    def __init__(self):
        AbstractHandler.__init__(self)
        self.dispersy_bootstrap_distribution = defaultdict(dict)

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "statistics-bootstrap-candidates"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        for key, value in json.iteritems():
            self.dispersy_bootstrap_distribution[key][node_nr] = value

    def all_files_done(self, extract_statistics):
        h_dispersy_bootstrap_distribution = open(os.path.join(extract_statistics.node_directory, "dispersy-bootstrap-distribution.txt"), "w+")
        print >> h_dispersy_bootstrap_distribution, "# sock_addr count"

        bootstrap_keys = self.dispersy_bootstrap_distribution.keys()
        bootstrap_keys.sort(cmp=lambda a, b: cmp(sum(self.dispersy_bootstrap_distribution[a].values()), sum(self.dispersy_bootstrap_distribution[b].values())), reverse=True)
        for sock_addr in bootstrap_keys:
            nodes = self.dispersy_bootstrap_distribution[sock_addr]
            times = sum(nodes.values())
            print >> h_dispersy_bootstrap_distribution, "%s %d" % (str(sock_addr), times)
        h_dispersy_bootstrap_distribution.close()


class DebugMessages(AbstractHandler):

    def __init__(self):
        AbstractHandler.__init__(self)

        self.dispersy_debugstatistics = set()

    def new_file(self, node_nr, filename, outputdir):
        self.outputdir = outputdir

    def filter_line(self, node_nr, line_nr, timestamp, timeoffset, key):
        return key == "scenario-debug"

    def handle_line(self, node_nr, line_nr, timestamp, timeoffset, key, json):
        for key, value in json.iteritems():
            if isinstance(value, (int, float)):
                self.write_to_debug(time, timeoffset, key, value)

    def write_to_debug(self, time, timeoffset, key, value):
        self.dispersy_debugstatistics.add(key)

        filename = os.path.join(self.outputdir, "scenario-%s-debugstatistics.txt" % key)
        if not os.path.exists(filename):
            h_debugstatistics = open(filename, "w+")
            print >> h_debugstatistics, "# timestamp timeoffset value"
        else:
            h_debugstatistics = open(filename, "a")

        print >> h_debugstatistics, time, timeoffset, value
        h_debugstatistics.close()

    def all_files_done(self, extract_statistics):
        for debug_stat in self.dispersy_debugstatistics:
            extract_statistics.merge_records("scenario-%s-debugstatistics.txt" % debug_stat, "scenario-%s-debugstatistics.txt" % debug_stat, 2)

def get_parser(argv):
    e = ExtractStatistics(argv[1])
    e.add_handler(BasicExtractor())
    e.add_handler(SuccMessages(argv[2]))
    e.add_handler(StatisticMessages())
    e.add_handler(DropMessages())
    e.add_handler(BootstrapMessages())
    e.add_handler(DebugMessages())
    return e

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print "Usage: %s <node-directory> <messagestoplot>" % (sys.argv[0])
        print >> sys.stderr, sys.argv

        sys.exit(1)

    e = get_parser(sys.argv)
    e.parse()
