from datetime import datetime


SORT_OPTIONS = [
    ("В порядке добавления", "insertion"),
    ("По ближайшему дедлайну", "deadline_asc"),
    ("По дальнему дедлайну", "deadline_desc"),
    ("По названию: А → Я", "title_asc"),
    ("По названию: Я → А", "title_desc"),
    ("Сначала новые", "created_desc"),
    ("Сначала старые", "created_asc"),
]


def parse_local_datetime(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(value)

        if result.tzinfo is not None:
            result = result.astimezone().replace(tzinfo=None)

        return result
    except (TypeError, ValueError):
        return None


def sort_group(tasks, mode):
    result = list(tasks)

    if mode in ("deadline_asc", "deadline_desc"):
        dated = []
        undated = []

        for task in result:
            deadline = parse_local_datetime(
                task.get("deadline")
            )

            if deadline is None:
                undated.append(task)
            else:
                dated.append((deadline, task))

        dated.sort(
            key=lambda item: item[0],
            reverse=(mode == "deadline_desc"),
        )

        # Задачи без срока всегда идут после задач со сроком.
        return [task for _, task in dated] + undated

    if mode in ("title_asc", "title_desc"):
        result.sort(
            key=lambda task: task.get("title", "").casefold(),
            reverse=(mode == "title_desc"),
        )

    elif mode in ("created_asc", "created_desc"):
        result.sort(
            key=lambda task: (
                parse_local_datetime(task.get("created"))
                or datetime.min
            ),
            reverse=(mode == "created_desc"),
        )

    return result


def sorted_tasks(sticker, include_done=True):
    tasks = [
        task
        for task in sticker["tasks"]
        if include_done or not task.get("done")
    ]

    mode = sticker.get("sort_mode", "deadline_asc")

    if sticker.get("done_last", True):
        unfinished = [
            task for task in tasks if not task.get("done")
        ]
        finished = [
            task for task in tasks if task.get("done")
        ]

        return (
            sort_group(unfinished, mode)
            + sort_group(finished, mode)
        )

    return sort_group(tasks, mode)