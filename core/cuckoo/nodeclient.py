# Copyright (C) 2020 - 2021 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import asyncio
from threading import Lock


from cuckoo.common.log import CuckooGlobalLogger
from cuckoo.common.clients import (
    ClientError, NodeEventReader, ResultRetrieverClient
)
from cuckoo.common.storage import TaskPaths, UnixSocketPaths

from cuckoo.node.node import (
    NodeError, InfoStreamReceiver, NodeTaskStates, NodeMsgTypes
)
from cuckoo.common.importing import NodeWorkZipper, AnalysisImportError

class NodeActionError(Exception):
    pass

log = CuckooGlobalLogger(__name__)

class AssignedTasks:

    def __init__(self):
        self._taskid_startabletask = {}
        self._lock = Lock()

    def have_task(self, task_id):
        return task_id in self._taskid_startabletask

    def track_assigned(self, startable_task):
        with self._lock:
            self._taskid_startabletask[startable_task.task.id] = startable_task

    def get_assigned(self, task_id):
        with self._lock:
            return self._taskid_startabletask[task_id]

    def untrack_assigned(self, task_id):
        with self._lock:
            self._taskid_startabletask.pop(task_id)


class LocalStreamReceiver(InfoStreamReceiver):

    def __init__(self):
        self.node_client = None

    def set_client(self, localnodeclient):
        self.node_client = localnodeclient

    def task_state(self, task_id, state):
        if state == NodeTaskStates.TASK_FAILED:
            self.node_client.task_failed(task_id)
        elif state == NodeTaskStates.TASK_DONE:
            self.node_client.task_done(task_id)
        else:
            log.error(
                "Unhandled task state update", task_id=task_id, state=state
            )

class NodeClientLoop:

    def __init__(self, loop):
        self.loop = loop
        loop.set_debug(True)
        self._task_stopper = {}
        self._asynctasks = set()
        self._stopped = False

    async def _cancel_asynctasks(self):
        for task in list(self._asynctasks):
            try:
                stopper = self._task_stopper.pop(task, None)
                if stopper:
                    try:
                        await stopper()
                    except Exception as e:
                        log.exception(
                            "Exception in stopper for asynctask",
                            stopper=stopper, asynctask=task, error=e
                        )
            finally:
                try:
                    print(f"Cancelling: {task}")
                    task.cancel()
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.exception(
                        "Unexpected error during asyncio task cancel", error=e
                    )

    async def _stop(self):
        try:
            await self._cancel_asynctasks()
        finally:
            self.loop.stop()

    def _cleanup(self):
        print("Calling cleanup")
        try:
            self.loop.close()
        except Exception as e:
            log.exception(
                "Unexpected error when closing asyncio event loop", error=e
            )

    def stop(self):
        self._stopped = True
        future = asyncio.run_coroutine_threadsafe(
            self._stop(), self.loop
        )

    def newtask_threadsafe(self, coro, args=(), done_cb=None, stopper_cb=None):
        if self._stopped:
            log.debug(
                "New asynctask after loop stopped. Will never start",
                coro=coro, args=args
            )
            return

        asyncio.run_coroutine_threadsafe(
            self.newtask(
                coro, args=args, done_cb=done_cb, stopper_cb=stopper_cb
            ), self.loop
        )

    async def newtask(self, coro, args=(), done_cb=None, stopper_cb=None):
        if self._stopped:
            log.debug(
                "New asynctask after loop stopped. Will never start",
                coro=coro, args=args
            )
            return

        async_task = self.loop.create_task(coro(*args))
        self._asynctasks.add(async_task)
        if stopper_cb:
            self._task_stopper[async_task] = stopper_cb

        def _done_cb(task):
            try:
                exp = task.exception()
                if exp:
                    log.exception(
                        "Asyncio task ended in unexpected error",
                        error=exp, exc_info=exp
                    )
            except asyncio.CancelledError:
                pass

            try:
                if done_cb:
                    done_cb()
            finally:
                self._asynctasks.discard(task)
                self._task_stopper.pop(task, None)

        async_task.add_done_callback(_done_cb)

    def start(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self._cleanup()

class NodeClient:

    @property
    def machines(self):
        raise NotImplementedError

    def task_failed(self, task_id):
        raise NotImplementedError

    def add_task(self, startable_task):
        raise NotImplementedError

class RemoteNodeClient(NodeClient):

    def __init__(self, cuckooctx, nodeapi_client, loop_wrapper):
        self.ctx = cuckooctx
        self.client = nodeapi_client
        self.loop_wrapper = loop_wrapper
        self.assigned_tasks = AssignedTasks()
        self.events = None

        self._machines = None
        self._events_open = False

    @property
    def name(self):
        return self.client.name

    @property
    def ready(self):
        return self._machines and self._events_open

    @property
    def machines(self):
        if not self._machines:
            raise NodeActionError("Machines list not loaded")

        return self._machines

    async def _event_msg(self, msgdict):
        msgtype = msgdict.get("type")
        if msgtype == NodeMsgTypes.TASK_STATE:
            task_id = msgdict.get("task_id")
            state = msgdict.get("state")
            if not self.assigned_tasks.have_task(task_id):
                return

            if state == NodeTaskStates.TASK_FAILED:
                await self.loop_wrapper.newtask(
                    self._task_failed, args=(task_id, True)
                )
            elif state == NodeTaskStates.TASK_DONE:
                await self.loop_wrapper.newtask(
                    self._task_done, args=(task_id,)
                )
            else:
                log.error("Unhandled task state", state=state, task_id=task_id)
        else:
            log.error("Unhandled message type", msgtype=msgtype)
            return

    def _event_read_end(self):
        self._events_open = False
        log.error("READ END")

    def _event_conn_err(self, e):
        self._events_open = False
        log.error("ERROR", error=e)

    def _event_conn_opened(self):
        self._events_open = True
        log.error("OPENED")

    async def start_reader(self):
        self.events = NodeEventReader(
            self.client, message_cb=self._event_msg,
            read_end_cb=self._event_read_end, conn_cb=self._event_conn_opened,
            connerr_cb=self._event_conn_err
        )

        try:
            await self.events.open()
        except ClientError as e:
            raise NodeActionError(f"Failed to open event reader. {e}")

        await self.loop_wrapper.newtask(
            self.events.read_stream, stopper_cb=self.events.close
        )

    def init(self):
        self.load_machine_list()

    def load_machine_list(self):
        try:
            self._machines = self.client.machine_list()
        except ClientError as e:
            raise NodeActionError(f"Failed retrieving machine list: {e}")

    async def _start_task(self, startable_task):
        try:
            await self.client.start_task(
                startable_task.task.id, startable_task.machine.name
            )
            log.error("START DONE")
        except ClientError as e:
            log.error("START ERROR", error=e)
            startable_task.log.error(
                "Failed to start remote task",
                task_id=startable_task.task.id, node=self.client.name,
                error=e
            )
            startable_task.errtracker.fatal_error(
                f"Failed to start remote task. {self.client.name}. {e}"
            )

            return False

        return True

    async def _upload_and_start(self, nodework, startable_task):
        log.error("In upload and start")
        with nodework:
            try:
                await self.client.upload_taskwork(nodework.path)
                log.error("Upload done")
            except ClientError as e:
                startable_task.log.error(
                    "Failed to upload work for task.",
                    task_id=startable_task.task.id, node=self.client.name,
                    error=e
                )
                startable_task.errtracker.fatal_error(
                    f"Failed to upload work to node. {self.client.name}. "
                    f"{e}"
                )
                return await self._task_failed(
                    startable_task.task.id, retrieve_result=False
                )
            finally:
                nodework.delete()

        if not await self._start_task(startable_task):
            return await self._task_failed(
                startable_task.task.id, retrieve_result=False
            )

    def add_task(self, startable_task):
        self.assigned_tasks.track_assigned(startable_task)

        try:
            nodework = NodeWorkZipper(
                startable_task.task.analysis_id, startable_task.task.id
            ).make_zip(TaskPaths.nodework_zip(startable_task.task.id))
        except AnalysisImportError as e:
            raise NodeActionError(f"Failed to create node work zip. {e}")

        log.error("Starting upload and start")
        self.loop_wrapper.newtask_threadsafe(
            self._upload_and_start, args=(nodework, startable_task)
        )

    def task_failed(self, task_id):
        # This is only called by the scheduler in case of some fail during
        # assigning.
        self.loop_wrapper.newtask_threadsafe(
            self._task_failed, args=(task_id, False),
        )

    async def _retrieve_result(self, startable_task):
        try:
            await ResultRetrieverClient.retrieve_result(
                UnixSocketPaths.result_retriever(), startable_task.task.id,
                self.client.name
            )
        except ClientError as e:
            startable_task.log.error(
                "Failed to retrieve result for task.",
                task_id=startable_task.task.id, node=self.client.name,
                error=e
            )
            startable_task.errtracker.fatal_error(
                f"Failed to retrieve result {self.client.name}. "
                f"{e}"
            )
            return False

        return True

    async def _task_failed(self, task_id, retrieve_result=True):
        startable_task = self.assigned_tasks.get_assigned(task_id)
        startable_task.release_resources()
        if retrieve_result:
            await self._retrieve_result(startable_task)

        try:
            startable_task.close()
        except Exception as e:
            log.exception(
                "Failed to close started task context", task_id=task_id,
                error=e
            )
        finally:
            self.ctx.state_controller.task_failed(
                task_id=startable_task.task.id,
                analysis_id=startable_task.task.analysis_id
            )
            self.assigned_tasks.untrack_assigned(task_id)

    async def _task_done(self, task_id):
        startable_task = self.assigned_tasks.get_assigned(task_id)
        startable_task.release_resources()

        if not await self._retrieve_result(startable_task):
            return await self._task_failed(task_id, retrieve_result=False)

        try:
            startable_task.close()
        except Exception as e:
            log.exception(
                "Failed to close started task context", task_id=task_id,
                error=e
            )
        finally:
            self.ctx.state_controller.task_done(
                task_id=startable_task.task.id,
                analysis_id=startable_task.task.analysis_id
            )
            self.assigned_tasks.untrack_assigned(task_id)


class LocalNodeClient(NodeClient):

    def __init__(self, cuckooctx, localnode):
        self.ctx = cuckooctx
        self.node = localnode
        self._machines = localnode.machinery.machines.copy()

        self.assigned_tasks = AssignedTasks()
        self._lock = Lock()
        cuckooctx.nodes.add_node(self)

    @property
    def machines(self):
        return self._machines

    def add_task(self, startable_task):
        self.assigned_tasks.track_assigned(startable_task)
        with self._lock:
            try:
                self.node.add_work(
                    startable_task.task.id, startable_task.machine.name
                )
            except NodeError as e:
                raise NodeActionError(e)

    def task_failed(self, task_id):
        startable_task = self.assigned_tasks.get_assigned(task_id)
        startable_task.release_resources()
        try:
            startable_task.close()
        except Exception as e:
            log.exception(
                "Failed to close started task context", task_id=task_id,
                error=e
            )
        finally:
            self.ctx.state_controller.task_failed(
                task_id=startable_task.task.id,
                analysis_id=startable_task.task.analysis_id
            )
            self.assigned_tasks.untrack_assigned(task_id)

    def task_done(self, task_id):
        startable_task = self.assigned_tasks.get_assigned(task_id)
        startable_task.release_resources()
        try:
            startable_task.close()
        except Exception as e:
            log.exception(
                "Failed to close started task context", task_id=task_id,
                error=e
            )
        finally:
            self.ctx.state_controller.task_done(
                task_id=startable_task.task.id,
                analysis_id=startable_task.task.analysis_id
            )
            self.assigned_tasks.untrack_assigned(task_id)