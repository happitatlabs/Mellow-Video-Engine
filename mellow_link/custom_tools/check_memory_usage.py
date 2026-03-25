def check_memory_usage():
    import psutil
    memory = psutil.virtual_memory()
    return {'total': memory.total, 'available': memory.available, 'percent': memory.percent, 'used': memory.used, 'free': memory.free}