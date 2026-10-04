"""
Patch _resolve_relative_date_filter in chat.py to align years and days intelligently.
"""
with open("app/rag/chat.py", "r", encoding="utf-8") as f:
    chat_content = f.read()

chat_content = chat_content.replace("\r\n", "\n")

old_resolve_block = """    @staticmethod
    def _resolve_relative_date_filter(filters: Dict[str, Any], records):
        \"\"\"Resolve recent windows and reject date requests outside data coverage.\"\"\"
        resolved = dict(filters)
        days = resolved.pop("relative_days", None)
        has_explicit_window = bool(resolved.get("date_from") or resolved.get("date_to"))
        if (
            not days and not has_explicit_window
            and resolved.get("month") is not None and resolved.get("year") is not None
        ):
            import calendar
            from datetime import date

            year, month = int(resolved["year"]), int(resolved["month"])
            resolved["date_from"] = date(year, month, 1).isoformat()
            resolved["date_to"] = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
            has_explicit_window = True
        if not days and not has_explicit_window:
            return resolved

        import pandas as pd

        if hasattr(records, "columns"):
            date_values = records["date"] if "date" in records.columns else None
        elif records:
            frame = pd.DataFrame(records)
            date_values = frame["date"] if "date" in frame.columns else None
        else:
            date_values = None
        if date_values is None:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        dates = pd.to_datetime(date_values, errors="coerce").dropna()
        if dates.empty:
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        first_available = dates.min().normalize()
        last_available = dates.max().normalize()
        if days:
            end = last_available
            start = end - pd.Timedelta(days=int(days) - 1)
            resolved["date_from"] = start.strftime("%Y-%m-%d")
            resolved["date_to"] = end.strftime("%Y-%m-%d")
        else:
            start = pd.to_datetime(resolved.get("date_from") or resolved.get("date_to"), errors="coerce")
            end = pd.to_datetime(resolved.get("date_to") or resolved.get("date_from"), errors="coerce")
            if pd.isna(start) or pd.isna(end):
                return resolved
            start, end = start.normalize(), end.normalize()

        if start < first_available or end > last_available:
            first_text = first_available.strftime("%Y-%m-%d")
            last_text = last_available.strftime("%Y-%m-%d")
            requested_text = (
                start.strftime("%Y-%m-%d") if start == end
                else f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            # Use the portion that exists in the selected data for explicit
            # calendar ranges (such as last month). Relative day-count windows
            # retain their full-window coverage requirement.
            clipped_start = max(start, first_available)
            clipped_end = min(end, last_available)
            if days or clipped_start > clipped_end:
                resolved["_date_filter_error"] = (
                    f"The requested period ({requested_text}) is outside the selected data's "
                    f"date coverage ({first_text} to {last_text}); I can't calculate sales for it."
                )
            else:
                resolved["date_from"] = clipped_start.strftime("%Y-%m-%d")
                resolved["date_to"] = clipped_end.strftime("%Y-%m-%d")
                resolved["_date_filter_note"] = (
                    f"Requested period was {requested_text}; the selected data covers "
                    f"{first_text} to {last_text}, so results use the overlapping dates only."
                )
        return resolved"""

new_resolve_block = """    @staticmethod
    def _resolve_relative_date_filter(filters: Dict[str, Any], records):
        \"\"\"Resolve recent windows, align implicit years with dataset range, and validate data coverage.\"\"\"
        resolved = dict(filters)
        days = resolved.pop("relative_days", None)
        has_explicit_window = bool(resolved.get("date_from") or resolved.get("date_to"))

        import pandas as pd

        if hasattr(records, "columns"):
            date_values = records["date"] if "date" in records.columns else None
        elif records:
            frame = pd.DataFrame(records)
            date_values = frame["date"] if "date" in frame.columns else None
        else:
            date_values = None
        if date_values is None:
            if not days and not has_explicit_window and resolved.get("month") is None:
                return resolved
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        dates = pd.to_datetime(date_values, errors="coerce").dropna()
        if dates.empty:
            if not days and not has_explicit_window and resolved.get("month") is None:
                return resolved
            resolved["_date_filter_error"] = (
                "The selected data has no usable transaction dates, so I can't "
                "calculate the requested date period."
            )
            return resolved

        first_available = dates.min().normalize()
        last_available = dates.max().normalize()

        # Check if query specified a month (and optional day) without an explicit year matching dataset
        if resolved.get("month") is not None:
            month = int(resolved["month"])
            day = int(resolved["day"]) if resolved.get("day") is not None else None
            explicit_year = resolved.get("year")

            if explicit_year is None:
                # Find available year in dataset matching this month
                matching_records = dates[dates.dt.month == month]
                if day is not None:
                    matching_day_records = matching_records[matching_records.dt.day == day]
                    if not matching_day_records.empty:
                        matching_records = matching_day_records
                if not matching_records.empty:
                    chosen_year = int(matching_records.dt.year.iloc[-1])
                    resolved["year"] = chosen_year
                    if day is not None:
                        resolved["date_from"] = f"{chosen_year:04d}-{month:02d}-{day:02d}"
                        resolved["date_to"] = f"{chosen_year:04d}-{month:02d}-{day:02d}"
                        has_explicit_window = True
                    else:
                        import calendar
                        from datetime import date
                        resolved["date_from"] = date(chosen_year, month, 1).isoformat()
                        resolved["date_to"] = date(chosen_year, month, calendar.monthrange(chosen_year, month)[1]).isoformat()
                        has_explicit_window = True
            elif explicit_year is not None:
                year = int(explicit_year)
                if day is not None:
                    resolved["date_from"] = f"{year:04d}-{month:02d}-{day:02d}"
                    resolved["date_to"] = f"{year:04d}-{month:02d}-{day:02d}"
                    has_explicit_window = True
                else:
                    import calendar
                    from datetime import date
                    resolved["date_from"] = date(year, month, 1).isoformat()
                    resolved["date_to"] = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
                    has_explicit_window = True

        if not days and not has_explicit_window:
            return resolved

        if days:
            end = last_available
            start = end - pd.Timedelta(days=int(days) - 1)
            resolved["date_from"] = start.strftime("%Y-%m-%d")
            resolved["date_to"] = end.strftime("%Y-%m-%d")
        else:
            start = pd.to_datetime(resolved.get("date_from") or resolved.get("date_to"), errors="coerce")
            end = pd.to_datetime(resolved.get("date_to") or resolved.get("date_from"), errors="coerce")
            if pd.isna(start) or pd.isna(end):
                return resolved
            start, end = start.normalize(), end.normalize()

            # If default year was applied outside data range, check if matching month/day exists in dataset
            if (start < first_available or end > last_available) and resolved.get("month") is not None and resolved.get("year") is None:
                month = int(resolved["month"])
                day = int(resolved["day"]) if resolved.get("day") is not None else None
                matching = dates[dates.dt.month == month]
                if day is not None:
                    matching = matching[matching.dt.day == day]
                if not matching.empty:
                    y = int(matching.dt.year.iloc[-1])
                    if day is not None:
                        start = pd.Timestamp(year=y, month=month, day=day)
                        end = start
                    else:
                        start = pd.Timestamp(year=y, month=month, day=1)
                        end = pd.Timestamp(year=y, month=month, day=matching.dt.day.max())
                    resolved["date_from"] = start.strftime("%Y-%m-%d")
                    resolved["date_to"] = end.strftime("%Y-%m-%d")

        if start < first_available or end > last_available:
            first_text = first_available.strftime("%Y-%m-%d")
            last_text = last_available.strftime("%Y-%m-%d")
            requested_text = (
                start.strftime("%Y-%m-%d") if start == end
                else f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            clipped_start = max(start, first_available)
            clipped_end = min(end, last_available)
            if days or clipped_start > clipped_end:
                resolved["_date_filter_error"] = (
                    f"The requested period ({requested_text}) is outside the selected data's "
                    f"date coverage ({first_text} to {last_text}); I can't calculate sales for it."
                )
            else:
                resolved["date_from"] = clipped_start.strftime("%Y-%m-%d")
                resolved["date_to"] = clipped_end.strftime("%Y-%m-%d")
                resolved["_date_filter_note"] = (
                    f"Requested period was {requested_text}; the selected data covers "
                    f"{first_text} to {last_text}, so results use the overlapping dates only."
                )
        return resolved"""

assert old_resolve_block in chat_content, "old_resolve_block not found in chat.py"
chat_content = chat_content.replace(old_resolve_block, new_resolve_block, 1)

with open("app/rag/chat.py", "w", encoding="utf-8") as f:
    f.write(chat_content)

print("chat.py _resolve_relative_date_filter patched successfully!")
